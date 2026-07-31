"""Semantic chunker — divide raw text into VERBATIM segments at meaning boundaries.

Front of the Feed pipeline. Takes a block of text Alex uploaded and returns the
pieces of it, cut where the meaning changes. It does not write any prose.

Standalone: this module knows nothing about the classifier, bp_architecture, or
knowledge_base. It does not write to any datastore.

**The stored text is the author's text.** Alex wrote "We assess the argument, not
the apparatus." An earlier version of this module stored "EpistemicOS assesses
the argument, not the apparatus" — a sentence he never wrote, in a system whose
entire premise is claim fidelity. Rewriting is now forbidden outright: no pronoun
resolution, no supplied subjects, no rephrasing, not one character.

Four rules drive the design:

1. **Cutting is semantic, so an LLM does it. Writing is not, so it does not.**
   The model chooses where the boundaries fall and nothing else. A 10-word input
   with one claim returns one segment; an 800-word document returns as many
   segments as it contains claims. No length heuristic is involved at any point.
2. **The text is sliced, not generated.** The model returns the substring it
   chose; this module locates it with ``str.find`` and then takes
   ``source[start:end]`` as the fact. So ``source[f.start_char:f.end_char] ==
   f.fact`` holds by construction, not by trusting the model to copy correctly —
   and ``audit_spans`` asserts it anyway. A segment that cannot be located is
   flagged UNLOCATED and refused by the write path.
3. **A segment that reads awkwardly alone is correct.** "It replaced the
   per-seat model" stays exactly that. Comprehension is separated from storage:
   the classifier receives the segment *plus the surrounding passage*, resolves
   the reference internally to decide where it belongs, and leaves the text
   alone. That is what a person does when asked where something files.
4. **Comprehension is free; discarding it is the waste.** The model reads the
   whole passage in one call, so it already knows which statements are claims,
   which are the evidence for them and which are recommendations that follow.
   Each fact carries a ``role`` and ``relationships`` to the other facts of its
   passage — which is what makes an unresolved segment usable later: its span,
   its neighbours and its links are all stored with it. An undeterminable link
   is omitted rather than guessed.
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


# Span audit outcomes. The old LLM fidelity audit (faithful / strengthened /
# weakened / unsupported / distorted) judged whether a REWRITE had drifted from
# the source. Nothing is rewritten now, so four of those five verdicts are
# unreachable by construction and the fifth is decidable without a model: either
# the segment is a substring of the source or it is not. The audit is therefore a
# string comparison, not a Bedrock call — cheaper, and it cannot itself be wrong.
VERBATIM = "verbatim"
UNLOCATED = "unlocated"
VALID_VERDICTS = {VERBATIM, UNLOCATED}

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

EXTRACT_PROMPT = """You divide raw business text into segments. You do not rewrite it.

Return a JSON array; each element is:
  {"id": <int, from 1, in output order>,
   "segment": "<an EXACT substring of the input, copied character for character>",
   "group": "<label of the list or frame this came from, or null>",
   "role": "<claim | evidence | recommendation | assessment | definition>",
   "relationships": {"supports": [ids], "supported_by": [ids],
                     "contrasts_with": [ids], "about": [ids]}}

=== VERBATIM — the rule everything else is subordinate to ===

"segment" must appear in the input EXACTLY as you write it. Copy it; do not
retype it from memory. It is looked up in the source with an exact string search
and discarded if it is not found.

FORBIDDEN, without exception:
  - Resolving a pronoun. "It replaced the per-seat model" stays exactly that.
    Never "The new pricing tier replaced the per-seat model."
  - Supplying a subject the text does not contain. "We assess the argument, not
    the apparatus." stays exactly that. Never "EpistemicOS assesses the
    argument, not the apparatus."
  - Rephrasing, tidying, expanding an abbreviation, fixing grammar, changing
    punctuation, normalising whitespace, or making a fragment into a sentence.
  - Adding or removing a single character.

A segment that reads awkwardly on its own is CORRECT. Later stages are given the
surrounding passage and resolve references themselves; they need the author's
words, not your improvement of them. The author wrote what they wrote.

If a claim cannot be captured as one exact substring, emit the smallest exact
substring that carries it, or omit it. Never invent connective text to join two
spans.

=== WHERE TO CUT ===

Cut at meaning boundaries. One segment = one thing that could independently be
true or false.

  - Cut a list of DISTINCT VALUES into one segment per value — each value is
    separately true or false.
    "8k for a department, 20k for a faculty" -> two segments:
      "8k for a department"  and  "20k for a faculty"
  - A list of SET MEMBERS under one predicate is ONE segment, role "definition".
    The membership is the claim; the members are not separate claims.
    "The depth engine archetype contains four tools: ManuSights Dossier,
     PaperReview.ai, RefineInk, Reviewer3"  -> ONE segment, the whole sentence.
    The test: does the passage predicate something DIFFERENT of each item
    (different prices, dates, costs -> cut), or are they interchangeable members
    of one named set (-> one segment)?
  - Keep a claim together with its own qualifier, scope, condition or exception.
    "Spain and the EU only, for the first eighteen months" is ONE segment.
    Cutting a claim from its qualifier creates two claims the source never made.
  - A reason ("because...", "which is why...") becomes its own segment only when
    it asserts something independently checkable. Otherwise keep it attached.
  - A claim spanning two sentences stays one segment.
  - Do NOT join distinct claims into one segment. "We target business schools and
    our price is 12k a year" is TWO segments: the target, and the price.
  - Segments follow source order and must not overlap.
  - Skip pure filler that asserts nothing (greetings, "as discussed", headers).
    Skipping is how you drop text — never by rewriting around it.

=== READ THE ARGUMENT BEFORE YOU CUT ===

You are given the whole passage at once, so read it as an argument first. Work
out which statements are the claims, which are the evidence offered for them,
which are recommendations following from that evidence, and which are judgements
about the other statements. Then cut, and carry that structure out with the
segments.

ROLES — what this segment is doing in the passage
  claim           an assertion about how things are
  evidence        an observation, measurement or example offered in support
  recommendation  what someone should do
  assessment      a judgement ABOUT another statement — its strength, importance
                  or status, rather than about the world
  definition      what something is, or what a named set contains

RELATIONSHIPS — reference the "id" of other segments from THIS passage
  supports        this segment is evidence for those segments
  supported_by    those segments are the evidence for this one
  contrasts_with  this segment is set against those ("X, not Y", "unlike Y")
  about           this segment is a judgement about those segments

Only link what the passage actually establishes. A link it does not make is a
defect; an omitted link is not. If you cannot tell, omit it.

Roles and links are metadata about the argument. They never change the text of a
segment and never merge two segments into one.

  Passage:
    Claim 1 — "We assess the argument, not the apparatus." The demonstrable
    market failure is that volume != coverage: the highest-output tool scored
    zero. EpistemicOS should lead with reviewer-risk coverage and explicitly
    refuse to compete on reference-and-typo count. This is the single strongest
    exhibit in the benchmark.

  1  claim           "We assess the argument, not the apparatus."
                     supported_by [2]
  2  evidence        "The demonstrable market failure is that volume != coverage:
                      the highest-output tool scored zero."      supports [1, 3]
  3  recommendation  "EpistemicOS should lead with reviewer-risk coverage and
                      explicitly refuse to compete on reference-and-typo count."
                     supported_by [2]
  4  assessment      "This is the single strongest exhibit in the benchmark."
                     about [2]

Note segment 4: "This" is left as "This". Resolving it is the next stage's job,
and it has the passage to do it with.

=== GROUPS — say when several segments came from ONE list ===

When you cut a single list, or several segments inherit ONE frame stated once (a
shared subject, unit, scope, currency or qualifier), give every member of that
set the SAME short "group" label naming the thing they share. Use null for a
segment that does not belong to such a set — most segments are null.

  "Eight thousand for a department, twenty thousand for a faculty, forty-five
   thousand campus-wide"
        -> three segments, all with "group": "subscription price tiers"

  "All figures below are per institution per year. Setup is two thousand,
   support is three thousand, training is one thousand."
        -> the three cost segments share "group": "per-institution annual cost items"
           (the framing sentence is its own segment, group null)

The label describes the list, not one member. Two segments that merely share a
topic are NOT a group. A group means the source wrote ONE list, and you cut it.

The group label is the ONLY place you may write words of your own, and it is
metadata — it never becomes part of any segment.

=== OTHER RULES ===

  - "id" must be unique within your array and match the element's position.
    Relationships may only reference ids you actually emit.
  - Keep numbers exactly as written. You are copying, so this is automatic.

Return ONLY the JSON array. No markdown fences, no commentary."""


@dataclass
class Fact:
    """One segment of a source text, held verbatim.

    ``fact`` is not generated. It is sliced out of the source at
    ``[start_char:end_char]``, so ``source[f.start_char:f.end_char] == f.fact``
    holds for every located fact by construction rather than by trust. The model
    chooses where the cuts go; it never supplies the text.

    Attributes:
        fact: The segment, character-for-character as the author wrote it.
            Pronouns are NOT resolved and subjects are NOT supplied — a segment
            reading "It replaced the per-seat model" is correct and stays that
            way. Comprehension happens at classification time, from the passage.
        source_quote: The same span. Retained because the stored metadata,
            `rerun_feed_27.py` and the roundtrip check all read it; with verbatim
            segments it is equal to ``fact`` and not an independent field.
        start_char: Offset of the segment in the source, or None if not located.
        end_char: End offset (exclusive), or None if not located.
        index: Position in the returned sequence, 0-based.
        needs_review: True when the segment could not be located in the source.
        verdict: VERBATIM when the span was verified, UNLOCATED when not.
        review_reason: Why the span audit flagged it, when it did.
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


def audit_spans(facts: list[Fact], source: str) -> list[Fact]:
    """Assert that every fact is an exact substring of its source, in place.

    The guarantee this module exists to provide::

        source[f.start_char:f.end_char] == f.fact

    ``chunk_text`` slices the text out of the source, so a mismatch here means a
    bug in this module rather than a model that paraphrased. It is checked
    anyway: the whole design rests on it, and an unchecked invariant is a belief.

    A fact whose span could not be located keeps the model's text but is flagged
    ``needs_review`` with verdict UNLOCATED, and ``feed_pipeline.confirm_card``
    refuses to store it. Not storing a claim Alex made is recoverable; storing
    words he did not write is what this change exists to stop.

    Args:
        facts: The facts to audit.
        source: The text they were cut from.

    Returns:
        The facts that failed the audit. Empty is the expected result.
    """
    failures: list[Fact] = []
    for f in facts:
        if f.start_char is None or f.end_char is None:
            f.needs_review = True
            f.verdict = UNLOCATED
            f.review_reason = (
                "segment could not be located in the source, so it cannot be "
                "shown to be the author's words — not storable"
            )
            failures.append(f)
            logger.error(
                "[Chunker] fact %d has no span in the source: %r",
                f.index,
                f.fact[:100],
            )
            continue

        if source[f.start_char : f.end_char] != f.fact:
            f.needs_review = True
            f.verdict = UNLOCATED
            f.review_reason = (
                "span does not match the stored text — the fact is not verbatim"
            )
            failures.append(f)
            logger.error(
                "[Chunker] fact %d span mismatch: source[%d:%d]=%r fact=%r",
                f.index,
                f.start_char,
                f.end_char,
                source[f.start_char : f.end_char][:100],
                f.fact[:100],
            )
            continue

        f.verdict = VERBATIM
        f.needs_review = False
        f.review_reason = None

    if failures:
        logger.error(
            "[Chunker] %d of %d facts failed the verbatim audit",
            len(failures),
            len(facts),
        )
    return failures


def chunk_text(
    text: str, model_id: Optional[str] = None, verify: bool = True
) -> list[Fact]:
    """Divide raw text into verbatim segments at meaning boundaries.

    Nothing is rewritten. Each returned fact is an exact substring of ``text``:
    pronouns are left unresolved, missing subjects are left missing, and a
    segment that reads awkwardly alone is returned that way. Understanding the
    segment is the classifier's job, and it is given the surrounding passage to
    do it with.

    Args:
        text: The raw text to divide. Any length; the cuts are semantic.
        model_id: Bedrock model id. Defaults to CLAUDE_SONNET_MODEL.
        verify: Accepted for compatibility and ignored — the span audit is a
            string comparison, always runs, and costs nothing. There is no
            longer an LLM verification pass to switch off.

    Returns:
        Facts in source order, each carrying its character span. Any fact whose
        span could not be verified is flagged ``needs_review`` with verdict
        UNLOCATED and is refused by the write path.
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
        # "segment" is the field the prompt asks for; "fact" is accepted so a
        # model that reverts to the old key still produces a located span rather
        # than a dropped one.
        segment = (item.get("segment") or item.get("fact") or "").strip()
        if not segment:
            logger.warning("[Chunker] Skipping element with empty segment at %d", i)
            continue

        start, end = _locate(segment, text, cursor)
        if start is not None:
            cursor = start
            # The stored text is sliced from the source, never taken from the
            # model. This is what makes source[start:end] == fact a property of
            # the code rather than a promise the prompt makes.
            segment = text[start:end]
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
                fact=segment,
                source_quote=segment,
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
    audit_spans(facts, text)

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
