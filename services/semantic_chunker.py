"""Semantic chunker — split raw text into atomic, self-contained facts.

Front of the Feed pipeline. Takes a block of text Alex uploaded and returns one
claim per fact, split at MEANING boundaries rather than at a fixed word count.

Standalone: this module knows nothing about the classifier, bp_architecture, or
knowledge_base. It does not write to any datastore.

Three rules drive the design:

1. **Splitting is semantic, so an LLM does it.** A 10-word input with one claim
   returns one fact; an 800-word document returns as many facts as it contains
   claims. No length heuristic is involved at any point.
2. **Provenance is computed, not generated.** The model returns a verbatim quote
   from the source; this module locates that quote with ``str.find`` to derive
   character offsets. Asking a model for character indices produces confident
   wrong numbers — models do not count characters reliably.
3. **A distorted fact is worse than a missing one.** Extraction preserves
   epistemic strength (a preference stays a preference, a hedge stays a hedge),
   and a second pass audits every fact against the source. Facts that fail are
   returned with ``needs_review=True``, never silently dropped or corrected.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from typing import Any, Optional

import boto3

logger = logging.getLogger(__name__)

_bedrock_client = None

DEFAULT_MODEL_ENV = "CLAUDE_SONNET_MODEL"
DEFAULT_MODEL = "us.anthropic.claude-sonnet-4-6"
MAX_TOKENS = 8192
MAX_RETRIES = 3
RETRY_BACKOFF = (1, 3, 8)

# Facts audited per verification call. A whole real document in one request
# times out on Bedrock's default 60s read timeout — see _verify.
VERIFY_BATCH_SIZE = 25

FAITHFUL = "faithful"
VALID_VERDICTS = {
    FAITHFUL,
    "strengthened",
    "weakened",
    "unsupported",
    "distorted",
}

EXTRACT_PROMPT = """You split raw business text into atomic facts.

An atomic fact is ONE self-contained claim. Return a JSON array; each element is:
  {"fact": "<the claim, rewritten to stand alone>",
   "source_quote": "<the exact substring of the input this came from>"}

=== PRESERVE EPISTEMIC STRENGTH — the most important rule ===

Extract what was said, at the strength it was said. Never upgrade or downgrade
how strongly a claim is held. A fact that reads cleanly but asserts more than
the source did is a defect, worse than omitting it.

  "we might move to Berlin"      -> "The company might move to Berlin."
                                 NOT "The company is moving to Berlin."
  "I'd rather find out in October"
                                 -> "The CEO would rather find out in October."
                                 NOT "The CEO expects to find out in October."
  "we're leaning toward per-seat"
                                 -> "The company is leaning toward per-seat pricing."
                                 NOT "The company chose per-seat pricing."
  "I suspect churn is seasonal"  -> "The CEO suspects churn is seasonal."
                                 NOT "Churn is seasonal."
  "roughly 40%"                  -> "roughly 40%"            NOT "40%"
  "up to fifteen percent"        -> "up to fifteen percent"  NOT "fifteen percent"
  "Sales think the price is high"
                                 -> "Sales think the price is high."
                                 NOT "The price is high."

Specifically:
  - A preference stays a preference. A plan stays a plan. They are different.
  - A hedge (might, maybe, probably, I think, seems, appears) stays hedged.
  - A possibility never becomes a certainty.
  - An opinion stays attributed to whoever holds it.
  - A question or an open item is not a decision.
  - Approximations, ranges and bounds keep their qualifier.
  - Conditionals keep their condition ("if the pilot works, we hire two more").

=== NEVER INFER WHO THE SPEAKER IS — hard rule, no exceptions ===

Render first person literally:
  "I", "me", "my"        -> "the speaker"
  "we", "us", "our"      -> "the company"  (when it clearly means the organisation)

NEVER map a first-person pronoun to a name, role, title or job. Not "the CEO",
not "the founder", not "Alex", not "the sales lead" — even when the surrounding
text makes it feel obvious.

  "I would rather find out in October"
        -> "The speaker would rather find out in October."
        NOT "The CEO would rather find out in October."
  "That is the gap I am most worried about"
        -> "The gap the speaker is most worried about is ..."
        NOT "The gap the CEO is most worried about is ..."

Why this is absolute: this pipeline ingests third-party documents — customer
interviews, emails, research notes, meeting transcripts — where the speaker is
NOT the person who uploaded the file. A customer saying "I would never pay that"
turned into "The CEO would never pay that" is a clean-looking fact that asserts
the opposite of the truth, and nothing downstream can detect it.

The only exception: if the text itself names or titles the speaker, you may use
what the text says. Never supply an identity the text does not state.

Resolving a pronoun to a referent named IN the text stays correct and required:
"It replaced the per-seat model" -> "The new pricing tier replaced the per-seat
model" when the text names that tier. Resolving a reference is not the same as
inventing an identity.

=== SPLITTING ===

One fact = one thing that could independently be true or false.

  - Split a list of parallel values into one fact per value.
    "8k for a department, 20k for a faculty" -> two facts.
  - Keep a claim together with its own qualifier, scope, condition or exception.
    "Spain and the EU only, for the first eighteen months" is ONE fact.
    Splitting a claim from its qualifier creates two claims the source never made.
  - A reason ("because...", "which is why...") becomes its own fact only when it
    asserts something independently checkable. Otherwise keep it attached.
  - A claim spanning two sentences stays one fact.
  - Do NOT merge distinct claims. "We target business schools and our price is
    12k a year" is TWO facts: target segment, and pricing.

=== OTHER RULES ===

  - Every fact must be SELF-CONTAINED. Resolve pronouns and references from the
    surrounding text. "This raised churn to 8%" becomes "The new pricing tier
    raised churn to 8%." Resolving a reference is required; it is not the same
    as adding information.
  - source_quote must be copied EXACTLY from the input, character for character —
    it is used to locate the fact in the source. Never paraphrase inside it.
  - Keep numbers exactly as written.
  - Skip pure filler that asserts nothing (greetings, "as discussed", headers).

Return ONLY the JSON array. No markdown fences, no commentary."""

VERIFY_PROMPT = """You audit extracted facts against the text they came from.

You receive the full source text and a numbered list of facts, each with the
quote it was drawn from. For each fact decide whether the source supports THAT
claim at THAT strength.

Return a JSON array, one element per fact, in the same order:
  {"index": <int>, "reason": "<one short sentence>", "verdict": "<verdict>"}

Write "reason" FIRST, then choose "verdict" to match what you just wrote. The
verdict is a conclusion drawn from the reason, not an independent judgement. If
your reason says the source supports the claim, the verdict is "faithful" — a
verdict that contradicts its own reason makes both untrustworthy.

VERDICTS

  faithful      The source supports this claim at this strength.
  strengthened  The fact asserts more confidence, certainty or commitment than
                the source did. A preference became a plan; a hedge became a
                statement; an opinion became a fact; "roughly 40%" became "40%".
  weakened      The fact hedges something the source stated plainly.
  unsupported   The fact asserts something the source does not say at all.
  distorted     The fact changes the meaning — wrong subject, wrong number,
                negation flipped, condition dropped.

RULES

  - Resolving a pronoun from elsewhere in the source is CORRECT, not unsupported.
    Source "It replaced the per-seat model" -> fact "The new pricing tier
    replaced the per-seat model" is faithful when the source names that tier.
  - Judge strength, not style. Rewording is fine; changing how strongly the
    claim is held is not.
  - Dropping a qualifier ("only", "up to", "roughly", "if X") is strengthened.
  - "the speaker" (for I/me/my) and "the company" (for we/us/our) are the
    REQUIRED extractor convention, NOT invented identities. Never flag them.
    Source "We are leaning toward X" -> fact "The company is leaning toward X"
    is FAITHFUL. Source "I would rather Y" -> "The speaker would rather Y" is
    FAITHFUL. These renderings appear in almost every document; flagging them
    would bury the real problems in noise.
  - What IS unsupported is a NAME, ROLE or TITLE the source never states:
    "The CEO would rather ...", "The founder decided ...", "Alex prefers ..."
    from a source that only says "I" or "we". Flag those.
  - Judge against the WHOLE source, not the quote alone. A qualifier stated once
    for a group carries to its members: if the source says "Pricing is an annual
    subscription" and later lists "eight thousand for a department", then "the
    department tier costs eight thousand per year" is FAITHFUL. Do not flag a
    detail that an adjacent sentence establishes.
  - Your verdict must match your reason. If your reasoning concludes the source
    supports the claim, return "faithful". Never return a non-faithful verdict
    alongside a reason that says the fact is accurate.
  - When the source genuinely supports the claim as written, say faithful. Do
    not invent problems.

Return ONLY the JSON array."""


@dataclass
class Fact:
    """One atomic claim extracted from a source text.

    Attributes:
        fact: The self-contained claim, with references resolved.
        source_quote: The verbatim span of the source the claim came from.
        start_char: Offset of source_quote in the source, or None if not located.
        end_char: End offset (exclusive), or None if not located.
        index: Position in the returned sequence, 0-based.
        needs_review: True when the verification pass did not return "faithful".
        verdict: The verification verdict, or None when verification was skipped.
        review_reason: Why the verifier flagged it, when it did.
    """

    fact: str
    source_quote: str
    start_char: Optional[int]
    end_char: Optional[int]
    index: int
    needs_review: bool = False
    verdict: Optional[str] = None
    review_reason: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Return the fact as a plain dict."""
        return asdict(self)


def _get_bedrock():
    """Lazy-load the Bedrock runtime client (singleton)."""
    global _bedrock_client
    if _bedrock_client is None:
        _bedrock_client = boto3.client(
            "bedrock-runtime",
            region_name=os.getenv("AWS_BEDROCK_REGION", "us-east-1"),
        )
        logger.info("[Chunker] Bedrock client initialized")
    return _bedrock_client


def _call_llm(system_prompt: str, user_text: str, model_id: str) -> str:
    """Send a prompt to Bedrock and return the raw response text.

    Retries throttling and transient errors with backoff. Raises on final
    failure — a chunker that silently returns nothing would drop Alex's input.
    """
    client = _get_bedrock()
    last_error: Optional[Exception] = None

    for attempt in range(MAX_RETRIES):
        try:
            response = client.converse(
                modelId=model_id,
                system=[{"text": system_prompt}],
                messages=[{"role": "user", "content": [{"text": user_text}]}],
                inferenceConfig={"maxTokens": MAX_TOKENS},
            )
            return response["output"]["message"]["content"][0]["text"]
        except Exception as exc:  # noqa: BLE001 — retried, then re-raised
            last_error = exc
            wait = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)]
            logger.warning(
                "[Chunker] LLM call failed (attempt %d/%d), retrying in %ds: %s",
                attempt + 1,
                MAX_RETRIES,
                wait,
                str(exc)[:160],
            )
            if attempt < MAX_RETRIES - 1:
                time.sleep(wait)

    logger.error("[Chunker] LLM call failed after %d attempts", MAX_RETRIES)
    raise RuntimeError(f"Bedrock call failed after {MAX_RETRIES} attempts") from last_error


def _parse_json_array(raw: str) -> list[Any]:
    """Parse a JSON array from a model response, tolerating markdown fences."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1] if "```" in cleaned[3:] else cleaned[3:]
        if cleaned.lstrip().startswith("json"):
            cleaned = cleaned.lstrip()[4:]
    cleaned = cleaned.strip().strip("`").strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.warning("[Chunker] Direct JSON parse failed (%s), trying repair", exc)

    try:
        from json_repair import repair_json

        return json.loads(repair_json(cleaned))
    except Exception as exc:  # noqa: BLE001 — reported, then raised
        logger.error("[Chunker] JSON repair failed: %s | raw head: %r", exc, raw[:200])
        raise ValueError("Could not parse chunker response as JSON") from exc


def _locate(quote: str, source: str, search_from: int) -> tuple[Optional[int], Optional[int]]:
    """Find a quote in the source and return (start, end) character offsets.

    Searches forward from ``search_from`` first so repeated phrases map to the
    occurrence the model was actually reading. Returns (None, None) when the
    quote cannot be located — recorded rather than guessed, so a bad span is
    visible instead of silently wrong.
    """
    if not quote:
        return None, None

    idx = source.find(quote, search_from)
    if idx == -1:
        idx = source.find(quote)
    if idx != -1:
        return idx, idx + len(quote)

    # Exact match failed. The usual cause is the model normalising whitespace
    # when it copies — a quote spanning a line break in a flattened table comes
    # back with a single space. Re-match on a whitespace-collapsed view of the
    # source and map the hit back to real offsets, so the span stays a true
    # offset into the ORIGINAL text rather than an approximation.
    collapsed_quote = " ".join(quote.split())
    if collapsed_quote:
        collapsed: list[str] = []
        offsets: list[int] = []
        previous_was_space = False
        for position, char in enumerate(source):
            if char.isspace():
                if not previous_was_space and collapsed:
                    collapsed.append(" ")
                    offsets.append(position)
                previous_was_space = True
                continue
            collapsed.append(char)
            offsets.append(position)
            previous_was_space = False

        hit = "".join(collapsed).find(collapsed_quote)
        if hit != -1:
            start = offsets[hit]
            end = offsets[hit + len(collapsed_quote) - 1] + 1
            logger.debug(
                "[Chunker] Located quote after whitespace normalisation: %r",
                quote[:60],
            )
            return start, end

    logger.warning("[Chunker] Could not locate quote in source: %r", quote[:80])
    return None, None


def _verify(facts: list[Fact], source: str, model_id: str) -> None:
    """Audit each fact against the source and set review fields in place.

    A verification failure marks every fact ``needs_review`` — an unaudited fact
    must not be mistaken for an audited one.

    Audited in batches. A real 800-word document yields ~170 facts, and sending
    all of them plus the source in one request produced a Bedrock read timeout
    that failed the whole pass and flagged every fact as unaudited. Batching
    also contains the blast radius: one timed-out batch costs its own facts
    their verdict, not the document's.
    """
    if not facts:
        return

    by_index: dict[int, dict] = {}
    failed: set[int] = set()

    for start in range(0, len(facts), VERIFY_BATCH_SIZE):
        batch = facts[start : start + VERIFY_BATCH_SIZE]
        listing = "\n".join(
            f'{f.index}. FACT: {f.fact}\n   QUOTE: "{f.source_quote}"' for f in batch
        )
        payload = (
            f'SOURCE TEXT:\n"""\n{source}\n"""\n\nFACTS TO AUDIT:\n{listing}'
        )
        try:
            results = _parse_json_array(_call_llm(VERIFY_PROMPT, payload, model_id))
        except Exception as exc:  # noqa: BLE001 — degrade to "unaudited", never silent
            logger.error(
                "[Chunker] Verification batch %d-%d failed: %s",
                batch[0].index,
                batch[-1].index,
                exc,
            )
            failed.update(f.index for f in batch)
            continue

        for item in results:
            if isinstance(item, dict) and isinstance(item.get("index"), int):
                by_index[item["index"]] = item

    for f in facts:
        if f.index in failed:
            f.needs_review = True
            f.verdict = None
            f.review_reason = "verification pass failed — fact is unaudited"
            continue
        item = by_index.get(f.index)
        if item is None:
            f.needs_review = True
            f.review_reason = "verifier returned no verdict for this fact"
            continue
        verdict = str(item.get("verdict", "")).strip().lower()
        if verdict not in VALID_VERDICTS:
            f.needs_review = True
            f.verdict = None
            f.review_reason = f"verifier returned an unrecognised verdict: {verdict!r}"
            continue
        f.verdict = verdict
        f.needs_review = verdict != FAITHFUL
        f.review_reason = str(item.get("reason", "")).strip() or None


def chunk_text(
    text: str, model_id: Optional[str] = None, verify: bool = True
) -> list[Fact]:
    """Split raw text into atomic, self-contained facts.

    Args:
        text: The raw text to split. Any length; splitting is semantic.
        model_id: Bedrock model id. Defaults to CLAUDE_SONNET_MODEL.
        verify: Run the fidelity audit. Facts that fail are returned with
            ``needs_review=True``; they are never dropped or rewritten.

    Returns:
        Facts in source order, each carrying its character span in the source.
        Empty list when the input contains no assertable claim.

    Raises:
        RuntimeError: The Bedrock call failed after retries.
        ValueError: The model's response could not be parsed as JSON.
    """
    if not text or not text.strip():
        logger.info("[Chunker] Empty input, returning no facts")
        return []

    model = model_id or os.getenv(DEFAULT_MODEL_ENV, DEFAULT_MODEL)
    items = _parse_json_array(_call_llm(EXTRACT_PROMPT, text, model))

    facts: list[Fact] = []
    cursor = 0
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            logger.warning("[Chunker] Skipping non-object element at %d: %r", i, item)
            continue
        claim = (item.get("fact") or "").strip()
        quote = (item.get("source_quote") or "").strip()
        if not claim:
            logger.warning("[Chunker] Skipping element with empty fact at %d", i)
            continue

        start, end = _locate(quote, text, cursor)
        if start is not None:
            cursor = start
        facts.append(
            Fact(
                fact=claim,
                source_quote=quote,
                start_char=start,
                end_char=end,
                index=len(facts),
            )
        )

    if verify:
        _verify(facts, text, model)

    flagged = sum(1 for f in facts if f.needs_review)
    unlocated = sum(1 for f in facts if f.start_char is None)
    logger.info(
        "[Chunker] %d chars -> %d facts (%d flagged, %d without a span) using %s",
        len(text),
        len(facts),
        flagged,
        unlocated,
        model,
    )
    return facts
