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
4. **Comprehension is free; discarding it is the waste.** The model reads the
   whole passage in one call, so it already knows which statements are claims,
   which are the evidence for them and which are recommendations that follow.
   Each fact therefore carries a ``role`` and ``relationships`` to the other
   facts of its passage. These are metadata for retrieval — every fact is still
   split, classified and filed independently, and an undeterminable link is
   omitted rather than guessed, which degrades to the behaviour before the
   fields existed.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from dataclasses import field as dataclass_field
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

# What a fact is doing in the passage it came from, and how it relates to the
# other facts in that passage. The chunker reads the whole text in one call, so
# it already has the argument structure; these fields stop it being discarded.
VALID_ROLES = {
    "claim",
    "evidence",
    "recommendation",
    "assessment",
    "definition",
}
VALID_RELATIONS = {
    "supports",
    "supported_by",
    "contrasts_with",
    "about",
}

EXTRACT_PROMPT = """You split raw business text into atomic facts.

An atomic fact is ONE self-contained claim. Return a JSON array; each element is:
  {"id": <int, from 1, in output order>,
   "fact": "<the claim, rewritten to stand alone>",
   "source_quote": "<the exact substring of the input this came from>",
   "group": "<label of the list or frame this came from, or null>",
   "role": "<claim | evidence | recommendation | assessment | definition>",
   "relationships": {"supports": [ids], "supported_by": [ids],
                     "contrasts_with": [ids], "about": [ids]}}

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

=== READ THE ARGUMENT BEFORE YOU SPLIT ===

You are given the whole passage at once, so read it as an argument before you cut
it up. Work out which statements are the claims, which are the evidence offered
for those claims, which are recommendations that follow from that evidence, and
which are judgements about the other statements. Then split, and carry that
structure out with the facts.

ROLES — what this fact is doing in the passage
  claim           an assertion about how things are
  evidence        an observation, measurement or example offered in support
  recommendation  what someone should do
  assessment      a judgement ABOUT another statement — its strength, importance
                  or status, rather than about the world
  definition      what something is, or what a named set contains

RELATIONSHIPS — reference the "id" of other facts from THIS passage
  supports        this fact is evidence for those facts
  supported_by    those facts are the evidence for this one
  contrasts_with  this fact is set against those ("X, not Y", "unlike Y")
  about           this fact is a judgement about those facts

Only link what the passage actually establishes. A link it does not make is a
defect; an omitted link is not. If you cannot tell, omit it — leave the list out
entirely rather than guessing.

Roles and links are metadata about the argument. They never change what a fact
SAYS, never merge two facts into one, and never excuse a fact from standing
alone. Every fact is still filed independently.

  Passage:
    Claim 1 — "We assess the argument, not the apparatus." The demonstrable
    market failure is that volume != coverage: the highest-output tool scored
    zero. EpistemicOS should lead with reviewer-risk coverage and explicitly
    refuse to compete on reference-and-typo count. This is the single strongest
    exhibit in the benchmark.

  1  claim           "EpistemicOS assesses the argument, not the apparatus."
                     supported_by [2]
  2  evidence        "The demonstrable market failure is that volume does not
                      equal coverage: the highest-output tool scored zero."
                     supports [1, 3]
  3  recommendation  "EpistemicOS should lead with reviewer-risk coverage and
                      explicitly refuse to compete on reference-and-typo count."
                     supported_by [2]
  4  assessment      "The volume-versus-coverage result is the single strongest
                      exhibit in the benchmark."
                     about [2]

=== SPLITTING ===

One fact = one thing that could independently be true or false.

  - Split a list of DISTINCT VALUES into one fact per value — each value is
    separately true or false.
    "8k for a department, 20k for a faculty" -> two facts.
  - A list of SET MEMBERS under one predicate is ONE fact, role "definition".
    The membership is the claim; the members are not separate claims.
    "The depth engine archetype contains four tools: ManuSights Dossier,
     PaperReview.ai, RefineInk, Reviewer3"  -> ONE fact.
    The test: does the passage predicate something DIFFERENT of each item
    (different prices, dates, costs -> split), or are they interchangeable
    members of one named set (-> one fact)?
  - Keep a claim together with its own qualifier, scope, condition or exception.
    "Spain and the EU only, for the first eighteen months" is ONE fact.
    Splitting a claim from its qualifier creates two claims the source never made.
  - A reason ("because...", "which is why...") becomes its own fact only when it
    asserts something independently checkable. Otherwise keep it attached.
  - A claim spanning two sentences stays one fact.
  - Do NOT merge distinct claims. "We target business schools and our price is
    12k a year" is TWO facts: target segment, and pricing.

=== GROUPS — say when several facts came from ONE list ===

When you split a single list, or several facts inherit ONE frame stated once
(a shared subject, unit, scope, currency or qualifier), give every member of that
set the SAME short "group" label naming the thing they share. Use null for a fact
that does not belong to such a set — most facts are null.

  "Eight thousand for a department, twenty thousand for a faculty, forty-five
   thousand campus-wide"
        -> three facts, all with "group": "subscription price tiers"

  "All figures below are per institution per year. Setup is two thousand,
   support is three thousand, training is one thousand."
        -> the three cost facts share "group": "per-institution annual cost items"
           (the framing sentence itself is its own fact, group null)

The label is a description of the list, not of one member. Two facts that merely
share a topic are NOT a group: "our price is twelve thousand" and "we expect
eighty percent renewal" are two unrelated claims that happen to sit in one
paragraph. A group means the source wrote ONE list, and you split it.

Never let a group change what a fact says. Grouping is a note about where the
fact came from; the fact itself must still stand alone, with its own qualifier
carried over from the frame ("Setup is two thousand per institution per year").

=== OTHER RULES ===

  - Every fact must be SELF-CONTAINED. Resolve pronouns and references from the
    surrounding text. "This raised churn to 8%" becomes "The new pricing tier
    raised churn to 8%." Resolving a reference is required; it is not the same
    as adding information.
  - source_quote must be copied EXACTLY from the input, character for character —
    it is used to locate the fact in the source. Never paraphrase inside it.
  - Keep numbers exactly as written.
  - Skip pure filler that asserts nothing (greetings, "as discussed", headers).
  - "id" must be unique within your array and must match the element's position.
    Relationships may only reference ids you actually emit.

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
        group_label: The model's name for the list or frame this fact was split
            out of, or None when it stands alone.
        group_id: Stable id shared by every member of one group, or None. Only
            assigned when a label has two or more members — a group of one is
            not a group.
        merged_spans: Provenance of near-duplicate facts collapsed into this one
            by services.fact_dedupe. Empty until dedupe runs.
        role: What this fact does in its passage — one of VALID_ROLES, or None
            when the model did not say or said something unrecognised.
        relationships: Links to other facts from the same passage, keyed by a
            member of VALID_RELATIONS, valued as ``Fact.index`` lists. Only
            relations with at least one resolved target appear; a fact with no
            determinable links carries an empty dict, which is the common case
            and is not a defect.
    """

    fact: str
    source_quote: str
    start_char: Optional[int]
    end_char: Optional[int]
    index: int
    needs_review: bool = False
    verdict: Optional[str] = None
    review_reason: Optional[str] = None
    group_label: Optional[str] = None
    group_id: Optional[str] = None
    merged_spans: list[dict[str, Any]] = dataclass_field(default_factory=list)
    role: Optional[str] = None
    relationships: dict[str, list[int]] = dataclass_field(default_factory=dict)

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


def _assign_group_ids(facts: list[Fact]) -> None:
    """Turn the model's free-text group labels into stable ids, in place.

    A label claimed by only one fact is dropped: the whole point of a group is
    that its members are classified together, and a group of one is just a fact.
    Labels are matched case- and whitespace-insensitively because the model
    re-types them per element and will not always match itself exactly.

    Args:
        facts: Facts carrying ``group_label`` from extraction.
    """
    counts: dict[str, int] = {}
    for f in facts:
        key = " ".join((f.group_label or "").lower().split())
        if key:
            counts[key] = counts.get(key, 0) + 1

    ids: dict[str, str] = {}
    for f in facts:
        key = " ".join((f.group_label or "").lower().split())
        if not key or counts[key] < 2:
            f.group_id = None
            continue
        if key not in ids:
            ids[key] = f"g{len(ids) + 1}"
        f.group_id = ids[key]

    if ids:
        logger.info(
            "[Chunker] %d group(s) tagged: %s",
            len(ids),
            ", ".join(
                f"{gid}={sum(1 for f in facts if f.group_id == gid)} facts"
                for gid in sorted(set(ids.values()))
            ),
        )


def _parse_role(raw: Any, position: int) -> Optional[str]:
    """Validate one element's role, or None.

    An unrecognised role is dropped rather than kept: a role nothing downstream
    understands is worse than no role, and no role is exactly what facts carried
    before this field existed.
    """
    role = str(raw or "").strip().lower()
    if not role:
        return None
    if role not in VALID_ROLES:
        logger.warning(
            "[Chunker] element %d: unrecognised role %r, dropped", position, role[:40]
        )
        return None
    return role


def _resolve_relationships(
    facts: list[Fact], raw: dict[int, Any], id_map: dict[int, int]
) -> None:
    """Map the model's element ids onto fact indices, in place.

    The model numbers its own output from 1; this turns those numbers into
    ``Fact.index`` values, which is what survives into the review card and the
    stored metadata. Elements the parse skipped are absent from ``id_map``, so
    links pointing at them resolve to nothing and are dropped.

    A link to an id that was never emitted, or to the fact itself, is dropped
    rather than repaired. An invented edge would be indistinguishable from one
    the passage established, and a missing edge only degrades to the behaviour
    before this field existed.

    Args:
        facts: The parsed facts, in output order.
        raw: The ``relationships`` object each fact came with, keyed by index.
        id_map: The model's element id -> ``Fact.index``.
    """
    for fact in facts:
        links = raw.get(fact.index)
        if not isinstance(links, dict):
            continue
        resolved: dict[str, list[int]] = {}
        for relation, targets in links.items():
            name = str(relation).strip().lower()
            if name not in VALID_RELATIONS:
                logger.debug(
                    "[Chunker] fact %d: unknown relation %r dropped",
                    fact.index,
                    str(relation)[:40],
                )
                continue
            if not isinstance(targets, list):
                continue
            picked: list[int] = []
            for target in targets:
                try:
                    mapped = id_map[int(target)]
                except (TypeError, ValueError, KeyError):
                    logger.debug(
                        "[Chunker] fact %d: %s -> %r names no emitted fact, dropped",
                        fact.index,
                        name,
                        target,
                    )
                    continue
                if mapped != fact.index and mapped not in picked:
                    picked.append(mapped)
            if picked:
                resolved[name] = picked
        fact.relationships = resolved


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
    # The model's element id -> the index the fact ended up with, and the raw
    # links each fact arrived with. Relationships are resolved in a second pass
    # because a fact can reference one that appears after it.
    id_map: dict[int, int] = {}
    raw_links: dict[int, Any] = {}

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
        raw_group = item.get("group")
        index = len(facts)

        # Fall back to position when the model omits or repeats an id: an
        # element that cannot be addressed can still be filed, it just cannot be
        # linked to.
        try:
            element_id = int(item["id"])
        except (KeyError, TypeError, ValueError):
            element_id = i + 1
        if element_id in id_map:
            logger.warning(
                "[Chunker] duplicate element id %d at position %d, not addressable",
                element_id,
                i,
            )
        else:
            id_map[element_id] = index
        raw_links[index] = item.get("relationships")

        facts.append(
            Fact(
                fact=claim,
                source_quote=quote,
                start_char=start,
                end_char=end,
                index=index,
                group_label=str(raw_group).strip() or None
                if isinstance(raw_group, str)
                else None,
                role=_parse_role(item.get("role"), i),
            )
        )

    _resolve_relationships(facts, raw_links, id_map)
    _assign_group_ids(facts)

    if verify:
        _verify(facts, text, model)

    flagged = sum(1 for f in facts if f.needs_review)
    unlocated = sum(1 for f in facts if f.start_char is None)
    grouped = sum(1 for f in facts if f.group_id)
    roled = sum(1 for f in facts if f.role)
    linked = sum(1 for f in facts if f.relationships)
    logger.info(
        "[Chunker] %d chars -> %d facts (%d flagged, %d without a span, "
        "%d in a group, %d with a role, %d linked) using %s",
        len(text),
        len(facts),
        flagged,
        unlocated,
        grouped,
        roled,
        linked,
        model,
    )
    return facts
