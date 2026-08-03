"""Feed classifier v3 — section-first retrieval, LLM judge over the full sibling set.

v2 handed the judge a top-k leaf shortlist, which caps accuracy at recall@k: the
correct node is simply absent from the set roughly half the time and no judge can
recover it. v3 changes what the judge is given, not how the judge thinks:

    embed fact -> rank SECTIONS -> hand the judge EVERY leaf in the chosen
    section(s), fully described -> judge picks one leaf or says "none fit"

Within a chosen section the candidate set is complete: the judge sees all 1-15
siblings (median 9), so no sibling can be missed for ranking reasons. The
remaining failure mode moves entirely to section selection.

**Measured limits, stated up front so nobody reads a false guarantee into this.**
Induced section recall on the 693-node content-complete pool is 38.9% @1 /
53.7% @2 on textbook facts and 20.0% @1 / 30.0% @2 on oblique paraphrases. So
"the answer is guaranteed present" is true only *within* a correctly chosen
section, and the section is chosen correctly well under half the time. This
architecture therefore trades coverage for precision: the judge's "none fit"
escape is what converts a wrong section into a review routing rather than a
wrong auto-file. Coverage is expected to be low; precision is the open question.

Sections are ranked by INDUCED similarity — rank leaves, map each to its parent,
dedupe in order. The alternative, scoring the fact against a section node's own
embedding, was measured at 7.4% @1 and is not used: section text is abstract
governance prose and does not discriminate.

Two rules carried forward unchanged from v2:

* **Retrieval sees degraded nodes; the gate decides.** Degraded leaves stay in
  the candidate set and are shown to the judge. Hiding them redirects a fact to
  a wrong-but-trusted node instead of protecting it.
* **A degraded leaf is never auto-filed.** If the judge picks a degraded leaf,
  the fact routes to BP.13 review carrying the node id and degraded_reason. See
  the degraded_target CONTRACT at the top of PROJECT_STATE.md.

⚠️ **`classify()` is no longer a filing path.** Seven retrieval mechanisms were
measured and the best leaf recall@10 is 57.4% — the correct node is absent from
the top ten for 43% of facts, against a 95% auto-file bar. The pipeline now
calls ``propose()`` instead, which returns a ranked shortlist for a human to
confirm from and commits to nothing. ``classify()`` is kept because the eval
harness scores it; do not wire it back into ingestion.

Standalone: reads bp_architecture, calls Bedrock, writes nothing.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Optional, Sequence

import boto3
import numpy as np

logger = logging.getLogger(__name__)

_bedrock_client = None

DEFAULT_MODEL_ENV = "CLAUDE_SONNET_MODEL"
DEFAULT_MODEL = "us.anthropic.claude-sonnet-4-6"
MAX_TOKENS = 2048
MAX_RETRIES = 3
RETRY_BACKOFF = (1, 3, 8)

# ---------------------------------------------------------------------------
# Hybrid section ranking (USE_HYBRID_RETRIEVAL)
#
# OFF by default. When on, section induction is driven by RRF fusion of the
# existing dense cosine with BM25 over node text, the BM25 index enriched with
# curated per-node operational vocabulary. Everything downstream is unchanged:
# same INDUCE_DEPTH window, same first-N-distinct-parents induction, same
# candidate_pool. Only the ORDER of the leaf walk differs.
#
# Measured on database/MEASUREMENT_KEY_v2.csv (n=112), candidate-set recall
# 42.9% -> 53.6% (+10.7), 17 rows gained against 5 lost. The gain is
# concentrated in BP.1 (+18.2) and BP.10 (+21.4); BP.8 barely moves because its
# leaves share templated required_output text and so carry no distinguishing
# vocabulary for BM25 to match.
#
# `best_leaf_similarity` stays the DENSE cosine even when ranking is hybrid, so
# `section_margin` and the review UI keep comparable numbers across the flag.
# ---------------------------------------------------------------------------
HYBRID_FLAG_ENV = "USE_HYBRID_RETRIEVAL"
OPERATIONAL_TERMS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "resources",
    "operational_terms_all.json",
)

_operational_terms: Optional[dict[str, list[str]]] = None


def hybrid_enabled() -> bool:
    """True when USE_HYBRID_RETRIEVAL is set to a truthy value."""
    return os.getenv(HYBRID_FLAG_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def _load_operational_terms() -> dict[str, list[str]]:
    """Curated per-node operational vocabulary, read once from the repo.

    A missing or unreadable file is not fatal: it degrades the BM25 side to
    unenriched node text rather than taking the pipeline down, and says so once.
    """
    global _operational_terms
    if _operational_terms is None:
        try:
            with open(OPERATIONAL_TERMS_PATH, encoding="utf-8") as handle:
                _operational_terms = json.load(handle).get("curated", {}) or {}
            logger.info(
                "[ClassifierV3] operational terms loaded for %d nodes from %s",
                len(_operational_terms),
                OPERATIONAL_TERMS_PATH,
            )
        except Exception as exc:  # noqa: BLE001 — logged, then degraded
            logger.error(
                "[ClassifierV3] could not read %s (%s) — hybrid BM25 will run "
                "on unenriched node text",
                OPERATIONAL_TERMS_PATH,
                str(exc)[:160],
            )
            _operational_terms = {}
    return _operational_terms


def _bm25_for(arch: Architecture) -> Any:
    """Build (once per Architecture) the BM25 index the hybrid arm fuses with."""
    index = getattr(arch, "_bm25_index", None)
    if index is None:
        from services.hybrid_retrieval import build_bm25

        index = build_bm25(arch, extra_terms=_load_operational_terms())
        arch._bm25_index = index  # noqa: SLF001 — cache on the loaded arch
    return index


# ---------------------------------------------------------------------------
# Candidate pool width (CANDIDATE_POOL_SIZE)
#
# UNSET BY DEFAULT, and unset means the historical behaviour exactly: the pool
# is whatever `sections_to_consider` sections contain (3 for propose(), median
# 30 nodes). Set it to a node count and the section count grows until the pool
# reaches that many candidates, capped at MAX_POOL_SECTIONS.
#
# It is a TARGET, not a hard cap — sections are taken whole, so the pool
# overshoots rather than truncating a section mid-way. Truncating would hand the
# judge a partial sibling set, which is the failure v3 was built to remove: the
# judge is meant to see every sibling in a chosen section (see module docstring).
#
# Measured on database/MEASUREMENT_KEY_v2.csv with hybrid on, in-pool recall by
# section count: 3 -> 52.7%, 4 -> 54.5%, 5 -> 55.4%, 6 -> 58.0%. Widening this
# way is a weak lever; a rank-based pool of the same size measured 68.8% at 50
# nodes. That would be a different pool shape, not a wider one, and is not what
# this knob does.
# ---------------------------------------------------------------------------
POOL_SIZE_ENV = "CANDIDATE_POOL_SIZE"
MAX_POOL_SECTIONS = 12


def configured_pool_size() -> Optional[int]:
    """Target candidate-pool node count from env, or None when unset.

    A malformed or non-positive value is ignored with a warning rather than
    raising: a bad env var should not take the pipeline down, and silently
    running the default is the safe direction.
    """
    raw = os.getenv(POOL_SIZE_ENV, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "[ClassifierV3] %s=%r is not an integer — using the default pool",
            POOL_SIZE_ENV,
            raw,
        )
        return None
    if value <= 0:
        logger.warning(
            "[ClassifierV3] %s=%d is not positive — using the default pool",
            POOL_SIZE_ENV,
            value,
        )
        return None
    return value


def _sections_for_target(
    arch: Architecture, sections: Sequence[SectionCandidate], target: int
) -> list[SectionCandidate]:
    """Take sections in rank order until their pool reaches `target` nodes."""
    chosen: list[SectionCandidate] = []
    for section in sections[:MAX_POOL_SECTIONS]:
        chosen.append(section)
        if len(candidate_pool(arch, chosen)) >= target:
            break
    return chosen


REVIEW_BUCKET = "BP.13"
AUTO_FILE = "auto_file"
PARENT_PARKED = "parent_parked"
REVIEW = "review"

NONE_FIT = "none"

# How deep to look when inducing the section ranking. Only affects which
# sections are *visible*, never which leaves the judge sees.
INDUCE_DEPTH = 120

# PROVISIONAL. The eval sweeps these over recorded per-fact outcomes; nothing
# here is tuned by hand. See scripts/eval_classifier_v3.py.
PROVISIONAL_SECTION_FLOOR = 0.0
PROVISIONAL_MARGIN_GATE = 0.0


@dataclass
class SectionCandidate:
    """One candidate section and the evidence that surfaced it."""

    section_id: str
    best_leaf_id: str
    best_leaf_similarity: float
    leaves_in_induced_window: int


@dataclass
class RoutingV3:
    """The routing decision for one fact, with the evidence behind it."""

    fact: str
    sections: list[SectionCandidate]
    section_margin: Optional[float]
    candidate_leaf_ids: list[str]
    judge_choice: Optional[str]
    judge_confidence: Optional[str]
    judge_reason: str
    decision: str
    target_node_id: Optional[str]
    parent_parked: bool = False
    leaf_degraded: bool = False
    leaf_degraded_reason: Optional[str] = None
    reason: str = ""
    thresholds_provisional: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return the routing as a plain dict."""
        return asdict(self)


JUDGE_PROMPT = """You file one atomic business fact into exactly one node of a \
business-plan architecture.

You are given the fact and the COMPLETE list of candidate nodes for one or two \
sections. The correct node is often, but NOT always, in this list. Saying "none" \
when nothing genuinely fits is a correct and valuable answer — a wrong filing is \
far worse than a deferred one.

Each node carries:
  purpose               what the node is for
  required_output       what must eventually be written there
  evidence_requirement  what kind of evidence that output needs
  prohibited_claims     inferences that must NOT be made at this node

=== HOW TO DECIDE ===
Ask: is this fact part of the REQUIRED OUTPUT of the node? Not "is it related to
the topic", not "does it share words with the title". A fact about who signs the
purchase order belongs at the node whose required output is the buyer definition,
even if another node's title contains the word "purchase".

Surface word overlap is the single most common way to get this wrong. A node
whose title repeats a phrase from the fact is NOT thereby the right node. Read
required_output before you look at any title.

If the fact would VIOLATE a node's prohibited_claims, that node is wrong.

=== CONFIDENCE ===
"high"  the fact is clearly part of this node's required output, and no other
        candidate covers it as directly
"low"   it belongs somewhere in this section but you cannot separate two or more
        siblings, or the fit is to the section's theme rather than to one node's
        required output

=== OUTPUT ===
Return one JSON object, nothing else:

{"reason": "<2-3 sentences: what the fact asserts, which node's required output \
covers it, and what you ruled out and why>",
 "choice": "<node_id, or the string \\"none\\">",
 "confidence": "<high or low>"}

Write "reason" FIRST and let the choice follow from it. Do not pick a node and \
then justify it.

If your reason says the fit is poor, weak or wrong, or that the node does not \
cover what the fact asserts, then "choice" MUST be "none". A choice that \
contradicts its own reason makes both untrustworthy."""


RANK_PROMPT = """You help a human file one atomic business fact into a \
business-plan architecture. You do NOT decide — you produce the shortlist they \
confirm from, so your job is to RANK and ANNOTATE, never to commit.

You are given the fact and the complete list of candidate nodes from the most \
plausible sections. Each node carries:
  purpose               what the node is for
  required_output       what must eventually be written there
  evidence_requirement  what kind of evidence that output needs
  prohibited_claims     inferences that must NOT be made at this node

=== HOW TO RANK ===
Ask: is this fact part of the REQUIRED OUTPUT of the node? Not "is it related to
the topic", not "does it share words with the title". Read required_output before
you look at any title — surface word overlap is the most common way to get this
wrong. A node whose title repeats a phrase from the fact is NOT thereby correct.

If the fact would VIOLATE a node's prohibited_claims, rank it last and say so.

=== THE FACT IS A VERBATIM SEGMENT — read the passage to understand it ===

The fact is an exact substring of the author's document. It was cut out, never
rewritten, so it may begin with "It", "This", "We" or "They", may lack a subject,
and may read as a fragment. That is intended and is not a defect to work around.

You are given THE PASSAGE it was cut from. Use it to work out what the segment
actually asserts: resolve the pronouns, recover the implied subject, carry over a
qualifier the passage states once for a list. Do that reasoning internally and
file the segment on the strength of it.

  passage  "The new tier launched in March. It replaced the per-seat model."
  segment  "It replaced the per-seat model"
  -> read as: the new tier replaced the per-seat model. File it where a pricing
     model change belongs, not where an unidentified "it" belongs.

Two hard limits on that resolution:

  - **Never rewrite the segment.** Not in your reason, not in a note, not
    anywhere. The author's words are what gets stored; your resolution exists
    only to place them.
  - **File the segment, never the passage.** The passage contains other claims
    with other homes. You are placing this segment alone.

If the passage does not resolve the reference, say so in your reason and rank on
what the segment does assert. An unresolvable segment is a candidate for review,
not a licence to guess a subject.

=== NEIGHBOURING FACTS ===

You may also be given the facts extracted either side of this one. They are
CONTEXT ONLY — you are filing the target fact, never a neighbour. Use them for
two decisions, and nothing else.

1. SUBSUMED. If everything the target fact asserts is already asserted by a
   neighbour that says MORE, the target is a partial of that neighbour and must
   not be filed at all. Set "subsumed_by" to that neighbour's label and return an
   empty candidates list.

     target    "EpistemicOS evaluates manuscript claims."
     neighbour "EpistemicOS evaluates manuscript claims before external review."
     -> subsumed: the neighbour asserts the same thing plus when it happens.

   Subsumed means STRICTLY CONTAINED. Two facts that overlap but each add
   something are both real facts — file the target normally.

     "The faculty tier is twenty thousand" and "Anything above the faculty tier
     needs the finance committee" share a subject and subsume neither way.

   A neighbour that is merely more general does NOT subsume a more specific
   target. Direction matters: the SUBSUMING fact is the one that says more.

2. PRIMARY CLAIM. When the target fact merges a main assertion with a
   subordinate or contrastive clause ("X, not Y", "X rather than Y", "X, unlike
   Y"), rank against the MAIN assertion. The subordinate clause says what the
   claim is not; it is not the claim.

     "The core problem is whether claims withstand epistemic scrutiny, not
      writing quality."
     -> the claim is what the core problem IS. File it at the node whose
        required output is the problem statement. Do NOT file it at a node about
        writing-quality scope just because that phrase appears.

   State in your reason which part you took as the main assertion.

=== LEAVES AND PARENTS ===

Most candidates are leaves. Some are the PARENT section of a group of leaves, and
are labelled as such. Rank a parent ONLY when the fact belongs in that section but
no single leaf under it covers it — a fact about the section's theme rather than
about one node's required output. Prefer a leaf whenever one genuinely fits.

A parent is a real answer, not a consolation. "This belongs in BP.8.4 but none of
its leaves is specifically about it" is far more useful to the human than an empty
shortlist, and far safer than forcing it onto the least-bad leaf.

=== WHAT MAKES A GOOD SHORTLIST ===
A human will scan your top few and click one. So:
  - Rank the genuinely plausible nodes first, best first.
  - Return AT MOST 5. A long list is slower to scan, not safer.
  - If two nodes are near-indistinguishable, keep both adjacent and say what
    separates them — that is the decision the human is actually making.
  - If NOTHING here fits, return an empty list. That is a useful answer: it
    sends the human to browse the tree instead of picking the least-bad option.

Each note is ONE short sentence, written to the human: what this node would mean
for this fact. Not a justification of your ranking.

=== THE NOTE DECIDES THE NODE, NEVER THE REVERSE ===

Within every candidate you write "note" first, then "fit", then "node_id". That
order is the rule, not formatting: the note is the reasoning and the ranking is
its conclusion. A note written after the id can only be a justification of a node
you had already picked.

"fit" is a verdict on what you just wrote in the note:
  "fits"  the note describes this node covering this fact
  "poor"  the note describes a mismatch — wrong subject, external thing filed as
          an internal one, the fact is about something this node does not cover,
          or filing here would misrepresent the fact

A candidate whose note argues against its own node MUST be marked "poor", and a
"poor" candidate MUST NOT appear in the list at all — delete it, do not rank it
lower. Never hand a human a ranked node annotated with the reason it is wrong:
they act on the rank, not on the note, and one shortlist that contradicts itself
makes every other ranking untrustworthy.

If every candidate you considered is poor, return an empty list. That is a
correct and useful answer.

=== OUTPUT ===
Return one JSON object, nothing else:

{"reason": "<one sentence: what the fact actually asserts>",
 "subsumed_by": "<neighbour label, or null>",
 "candidates": [{"note": "<one sentence to the human>",
                 "fit": "<fits or poor>",
                 "node_id": "<id>"}, ...]}

Write "reason" first and let the ranking follow from it. Inside each candidate
write "note", then "fit", then "node_id", in that order."""


GROUP_RANK_PROMPT = """You help a human file a LIST of atomic business facts into \
a business-plan architecture. You do NOT decide — you produce the shortlist they \
confirm from, so your job is to RANK and ANNOTATE, never to commit.

These facts were ONE list in the source document, cut into atomic claims. They
belong together: the parallel values of one pricing table, the line items of one
cost breakdown. Your job is to choose ONE shortlist for the WHOLE list.

Each member is a VERBATIM segment of the author's document — cut out, never
rewritten — so a member may read as a fragment ("twenty thousand for a faculty")
and the frame it depends on may sit in the passage rather than in the member. You
are given THE PASSAGE. Use it to understand what the list asserts; never rewrite
a member, and never file the passage.

This matters because filing the members separately is how a single table ends up
scattered across three nodes. Whatever node the list belongs at, every member
belongs at that same node. Do not try to separate them.

You are given every member of the list and the complete candidate nodes from the
most plausible sections. Each node carries:
  purpose               what the node is for
  required_output       what must eventually be written there
  evidence_requirement  what kind of evidence that output needs
  prohibited_claims     inferences that must NOT be made at this node

=== HOW TO RANK ===
Ask: is this LIST, as a whole, part of the REQUIRED OUTPUT of the node? Read
required_output before you look at any title. A node that fits one member but not
the others is the wrong node — the right one covers the whole set.

If the list would VIOLATE a node's prohibited_claims, say so and mark it poor.
Return AT MOST 5 candidates, best first. If nothing fits the list, return an
empty list.

Some candidates are the PARENT section of a group of leaves and are labelled as
such. Rank a parent ONLY when the list belongs in that section but no single leaf
under it covers it. Prefer a leaf whenever one genuinely fits.

Each note is ONE short sentence to the human: what filing this list at that node
would mean.

=== THE NOTE DECIDES THE NODE, NEVER THE REVERSE ===

Within every candidate write "note" first, then "fit", then "node_id". The note
is the reasoning; the ranking is its conclusion.

  "fits"  the note describes this node covering this list
  "poor"  the note describes a mismatch

A candidate whose note argues against its own node MUST be marked "poor", and a
"poor" candidate MUST NOT appear in the list at all — delete it, do not rank it
lower. If every candidate is poor, return an empty list.

=== OUTPUT ===
Return one JSON object, nothing else:

{"reason": "<one sentence: what this list collectively asserts>",
 "candidates": [{"note": "<one sentence to the human>",
                 "fit": "<fits or poor>",
                 "node_id": "<id>"}, ...]}

Write "reason" first and let the ranking follow from it. Inside each candidate
write "note", then "fit", then "node_id", in that order."""


def _get_bedrock():
    """Lazy-load the Bedrock runtime client (singleton)."""
    global _bedrock_client
    if _bedrock_client is None:
        _bedrock_client = boto3.client(
            "bedrock-runtime",
            region_name=os.getenv("AWS_BEDROCK_REGION", "us-east-1"),
        )
        logger.info("[ClassifierV3] Bedrock client initialized")
    return _bedrock_client


def _call_llm(system_prompt: str, user_text: str, model_id: str) -> str:
    """Send a prompt to Bedrock and return the raw response text.

    Retries throttling and transient errors with backoff, then raises. A judge
    that silently returned nothing would look identical to "none fit", which
    would corrupt the precision measurement.
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
                "[ClassifierV3] judge call failed (attempt %d/%d), retry in %ds: %s",
                attempt + 1,
                MAX_RETRIES,
                wait,
                str(exc)[:160],
            )
            if attempt < MAX_RETRIES - 1:
                time.sleep(wait)

    logger.error("[ClassifierV3] judge failed after %d attempts", MAX_RETRIES)
    raise RuntimeError(
        f"Bedrock call failed after {MAX_RETRIES} attempts"
    ) from last_error


def _require_object(parsed: Any) -> dict[str, Any]:
    """Enforce this module's parse contract: the judge returns a JSON OBJECT.

    ``json.loads`` happily returns a list, string or number, and ``repair_json``
    will coerce a prose response into a list — so without this check a non-object
    reaches ``_rank_from_parsed``, which does ``parsed.get("candidates", [])``
    and dies with AttributeError far from the cause. Raising here turns it into
    the ValueError callers already expect from an unusable judge response.

    Observed once in ~450 judge calls, so rare, but the prose-preamble responses
    that make it possible are not (they hit the repair path on roughly 10%).
    """
    if not isinstance(parsed, dict):
        raise ValueError(f"judge returned a {type(parsed).__name__}, expected object")
    return parsed


def _parse_json_object(raw: str) -> dict[str, Any]:
    """Parse a JSON object from a model response, tolerating markdown fences.

    Raises:
        ValueError: the response is unparseable, or parses to something other
            than a JSON object.
    """
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1] if "```" in cleaned[3:] else cleaned[3:]
        if cleaned.lstrip().startswith("json"):
            cleaned = cleaned.lstrip()[4:]
    cleaned = cleaned.strip().strip("`").strip()

    try:
        return _require_object(json.loads(cleaned))
    except json.JSONDecodeError as exc:
        logger.warning("[ClassifierV3] JSON parse failed (%s), trying repair", exc)

    try:
        from json_repair import repair_json

        return _require_object(json.loads(repair_json(cleaned)))
    except Exception as exc:  # noqa: BLE001 — logged, then surfaced as a failure
        logger.error("[ClassifierV3] JSON repair failed: %s", exc)
        raise ValueError(f"judge returned unparseable output: {raw[:200]}") from exc


def parent_of(node_id: str) -> str:
    """Return the sibling group (immediate parent) of a node id."""
    return ".".join(node_id.split(".")[:-1])


@dataclass
class Architecture:
    """The loaded architecture: leaf vectors, node metadata, sibling index."""

    leaf_ids: list[str]
    leaf_matrix: np.ndarray
    nodes: dict[str, dict[str, Any]]
    siblings: dict[str, list[str]]


ARCH_COLUMNS = (
    "node_id,parent_node,node_title,purpose,required_output,evidence_requirement,"
    "prohibited_claims_inference_patterns,degraded_target,degraded_reason,embedding"
)


def get_architecture_client() -> Any:
    """Client for bp_architecture.

    The table has RLS on and the anon role sees zero rows, so the service-role
    key is required. Falling back to the anon client silently would produce an
    empty architecture and a confusing crash far from the cause.
    """
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise ValueError(
            "[ClassifierV3] SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are "
            "required — bp_architecture is not readable by the anon role"
        )
    if not hasattr(get_architecture_client, "_client"):
        from supabase import create_client

        get_architecture_client._client = create_client(url, key)
    return get_architecture_client._client


def node_sort_key(node_id: str) -> tuple:
    """Sort key that orders BP.8.1.2 before BP.8.1.10.

    Lexicographic ordering puts ".10" before ".2", which scrambles the slot
    sequence in every section with ten or more children — and those are the
    sections where the ordering matters most. Numeric segments sort as numbers;
    anything non-numeric falls back to its string.
    """
    parts: list[tuple[int, Any]] = []
    for piece in node_id.split("."):
        if piece.isdigit():
            parts.append((0, int(piece)))
        else:
            parts.append((1, piece))
    return tuple(parts)


def load_architecture(supabase_client: Any = None) -> Architecture:
    """Load every node, build the childless-leaf matrix and the sibling index.

    A "leaf" is a node with no children, not a node at depth >= 3. BP.11.7 sits
    at depth 2 with zero children and is a legitimate filing target.

    **The query is ordered, and that is a correctness requirement, not tidiness.**
    PostgREST `.range()` pagination over an unordered query has no stable row
    order, so successive pages could repeat or skip rows. It also left
    ``siblings`` in whatever order the API happened to return — 42 of 91 sections
    came back out of node_id order — which scrambled the repeating slot sequence
    (taxonomy, classification, assumptions, evidence requirements, prohibited
    inferences, acceptance check) that every section shares and that the judge
    sees as its candidate list.
    """
    supabase_client = supabase_client or get_architecture_client()
    rows: list[dict[str, Any]] = []
    start = 0
    while True:
        resp = (
            supabase_client.table("bp_architecture")
            .select(ARCH_COLUMNS)
            .order("node_id")
            .range(start, start + 99)
            .execute()
        )
        if not resp.data:
            break
        rows += resp.data
        start += 100

    all_ids = {r["node_id"] for r in rows}
    parents = {parent_of(i) for i in all_ids}
    leaf_rows = [
        r for r in rows if r["node_id"] not in parents and r["embedding"] is not None
    ]
    if not leaf_rows:
        raise RuntimeError(
            f"[ClassifierV3] bp_architecture returned {len(rows)} rows and no "
            f"embedded leaves — check the service-role key and that the bulk "
            f"embed has run"
        )

    matrix = np.vstack(
        [
            np.asarray(
                (
                    json.loads(r["embedding"])
                    if isinstance(r["embedding"], str)
                    else r["embedding"]
                ),
                dtype=np.float32,
            )
            for r in leaf_rows
        ]
    )
    matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)

    nodes = {r["node_id"]: r for r in rows}
    siblings: dict[str, list[str]] = {}
    for r in leaf_rows:
        siblings.setdefault(parent_of(r["node_id"]), []).append(r["node_id"])
    # Sorted numerically, not lexicographically: the judge reads this list in
    # order and the slot sequence is only legible if .2 precedes .10.
    for section in siblings:
        siblings[section].sort(key=node_sort_key)

    logger.info(
        "[ClassifierV3] %d nodes, %d embedded leaves, %d sibling groups",
        len(rows),
        len(leaf_rows),
        len(siblings),
    )
    return Architecture(
        leaf_ids=[r["node_id"] for r in leaf_rows],
        leaf_matrix=matrix,
        nodes=nodes,
        siblings=siblings,
    )


def rank_sections(
    fact_vector: list[float],
    arch: Architecture,
    top_n: int = 2,
    fact_text: Optional[str] = None,
) -> list[SectionCandidate]:
    """Rank sections by induced similarity: best leaf in each section wins.

    Direct scoring against section embeddings measured 7.4% @1 and is not used.

    Args:
        fact_vector: The embedded fact.
        arch: A loaded Architecture.
        top_n: How many distinct sections to keep.
        fact_text: The fact's raw text. Required for the hybrid ranking signal,
            which needs the query terms BM25 scores against. Omitting it (or
            leaving USE_HYBRID_RETRIEVAL off) keeps the dense-only ranking.

    Returns:
        The chosen sections. `best_leaf_similarity` is the dense cosine of that
        section's best leaf under either ranking, so the value stays comparable
        across the flag — only the ORDER of the walk changes.
    """
    q = np.asarray(fact_vector, dtype=np.float32)
    q /= np.linalg.norm(q)
    sims = arch.leaf_matrix @ q

    ranking = sims
    if fact_text and hybrid_enabled():
        from services.hybrid_retrieval import fuse

        ranking = fuse(sims, _bm25_for(arch).scores(fact_text), mode="rrf")

    order = np.argsort(-ranking)[:INDUCE_DEPTH]

    seen: dict[str, SectionCandidate] = {}
    for j in order:
        node_id = arch.leaf_ids[int(j)]
        section = parent_of(node_id)
        if section in seen:
            seen[section].leaves_in_induced_window += 1
        elif len(seen) < top_n:
            seen[section] = SectionCandidate(
                section_id=section,
                best_leaf_id=node_id,
                best_leaf_similarity=round(float(sims[int(j)]), 4),
                leaves_in_induced_window=1,
            )
    return list(seen.values())


def _describe(node: dict[str, Any]) -> str:
    """Render one candidate node for the judge."""

    def field_text(key: str) -> str:
        value = (node.get(key) or "").strip()
        return value if value and value != "None" else "(not specified)"

    lines = [
        f"node_id: {node['node_id']}",
        f"title: {field_text('node_title')}",
        f"purpose: {field_text('purpose')}",
        f"required_output: {field_text('required_output')}",
        f"evidence_requirement: {field_text('evidence_requirement')}",
        f"prohibited_claims: {field_text('prohibited_claims_inference_patterns')}",
    ]
    if node.get("degraded_target"):
        lines.append(
            f"NOTE: this node's definition is degraded ({node.get('degraded_reason')})"
        )
    return "\n".join(lines)


def candidate_pool(
    arch: Architecture, sections: Sequence[SectionCandidate]
) -> list[str]:
    """Every leaf in the chosen sections, plus each SECTION NODE itself.

    The section node is the parent fallback. Routing has always been stated as
    leaf -> section -> review, but `propose()` built its pool from
    ``arch.siblings`` alone, which contains only leaves — so a fact that belonged
    to a section but to no single leaf under it had no legal target and fell
    straight through to review. The rule existed in `classify()` (PARENT_PARKED)
    and nowhere on the path that actually files.

    A section is only a filing target when no leaf earns the fact; the judge is
    told this and each parent is labelled in the rendered candidate list.

    Args:
        arch: A loaded Architecture.
        sections: The chosen sections, in rank order.

    Returns:
        Candidate node ids: each section's leaves, then the section itself.
    """
    ids: list[str] = []
    for section in sections:
        for leaf in arch.siblings.get(section.section_id, []):
            if leaf not in ids:
                ids.append(leaf)
        if section.section_id in arch.nodes and section.section_id not in ids:
            ids.append(section.section_id)
    return ids


def is_section(arch: Architecture, node_id: str) -> bool:
    """True when a node has leaves under it, i.e. it is a parent not a leaf."""
    return bool(arch.siblings.get(node_id))


def _build_judge_input(
    fact: str,
    candidate_ids: list[str],
    arch: Architecture,
    siblings: Optional[Sequence[str]] = None,
    passage: Optional[str] = None,
) -> str:
    """Compose the judge's user message: the fact plus every candidate node.

    The passage, when given, is the source text around the segment. It is what
    lets the judge resolve "it" and "we" in a verbatim segment without anyone
    rewriting the segment to do it. Siblings are labelled S1..Sn; the labels are
    what the judge returns in ``subsumed_by``, so they must be stable within one
    call.
    """
    blocks = [
        _describe(arch.nodes[n])
        + (
            "\nNOTE: this is a PARENT section — prefer a leaf under it."
            if is_section(arch, n)
            else ""
        )
        for n in candidate_ids
    ]
    parts = [f"FACT TO FILE (verbatim segment of the source):\n{fact}"]
    if passage:
        parts.append(
            "THE PASSAGE IT WAS CUT FROM (for comprehension only — resolve "
            "references from it, never file it, never rewrite the segment):\n"
            f'"""\n{passage}\n"""'
        )
    if siblings:
        listing = "\n".join(f"  S{i + 1}. {s}" for i, s in enumerate(siblings))
        parts.append(
            "NEIGHBOURING FACTS (context only — do not file these):\n" + listing
        )
    parts.append(
        f"CANDIDATE NODES ({len(candidate_ids)}):\n\n" + "\n---\n".join(blocks)
    )
    return "\n\n".join(parts)


def _build_group_input(
    facts: Sequence[str],
    candidate_ids: list[str],
    arch: Architecture,
    passage: Optional[str] = None,
) -> str:
    """Compose the group judge's user message: every member plus the candidates."""
    blocks = [
        _describe(arch.nodes[n])
        + (
            "\nNOTE: this is a PARENT section — prefer a leaf under it."
            if is_section(arch, n)
            else ""
        )
        for n in candidate_ids
    ]
    listing = "\n".join(f"  {i + 1}. {f}" for i, f in enumerate(facts))
    parts = [f"LIST TO FILE ({len(facts)} verbatim segments from one list):\n{listing}"]
    if passage:
        parts.append(
            "THE PASSAGE THEY WERE CUT FROM (for comprehension only — resolve "
            "references from it, never file it, never rewrite a member):\n"
            f'"""\n{passage}\n"""'
        )
    parts.append(
        f"CANDIDATE NODES ({len(candidate_ids)}):\n\n" + "\n---\n".join(blocks)
    )
    return "\n\n".join(parts)


PROPOSED = "proposed"
NO_PROPOSAL = "no_proposal"
SUBSUMED = "subsumed"
SHORTLIST_SIZE = 5

# The judge's per-candidate verdict on its own note.
FIT_OK = "fits"
FIT_POOR = "poor"

# Phrases that mean a note has judged its own node wrong. The judge is asked to
# say so in `fit`; this is the backstop for when it writes the rejection in prose
# and labels the candidate "fits" anyway — which is the observed failure, not a
# hypothetical one: five cards shipped as ready carrying notes reading "A POOR
# FIT since these are external competitor tools, not internal capabilities".
# Matching is substring, lowercase, and deliberately errs toward dropping: a
# false positive costs one shortlist entry, a false negative files a fact at a
# node its own annotation rejects.
_REJECTING_PHRASES = (
    "poor fit",
    "poor match",
    "bad fit",
    "weak fit",
    "not a fit",
    "not a good fit",
    "not a strong fit",
    "does not fit",
    "doesn't fit",
    "do not fit",
    "don't fit",
    "does not belong",
    "doesn't belong",
    "does not cover",
    "doesn't cover",
    "wrong node",
    "wrong section",
    "wrong place",
    "not the right node",
    "not the right place",
    "misfile",
    "misfiling",
    "misrepresent",
    "mischaracteris",
    "mischaracteriz",
    "ill-suited",
    "unsuitable",
    "not appropriate",
    "would be incorrect",
    "would be wrong",
)


def _note_rejects(note: str) -> bool:
    """True when a candidate's own note argues against the node it annotates."""
    lowered = note.lower()
    return any(phrase in lowered for phrase in _REJECTING_PHRASES)


@dataclass
class ProposedNode:
    """One ranked candidate on a review card."""

    rank: int
    node_id: str
    title: Optional[str]
    section_id: str
    note: str
    degraded: bool
    degraded_reason: Optional[str]
    # "leaf" or "section". A section-level match is the parent fallback: the
    # fact belongs in that section but no leaf under it covers it. Recorded so
    # the reviewer can see they are being offered a parent, and so a
    # section-level filing is distinguishable downstream from a leaf filing.
    level: str = "leaf"


@dataclass
class Proposal:
    """A ranked shortlist for one fact, for a human to confirm from.

    There is no decision here beyond whether a shortlist exists. Nothing files
    anything; the confirmed node is written by a human, elsewhere.
    """

    fact: str
    decision: str
    candidates: list[ProposedNode]
    sections: list[SectionCandidate]
    section_margin: Optional[float]
    considered_leaf_ids: list[str]
    reason: str = ""
    subsumed_by: Optional[str] = None
    group_id: Optional[str] = None
    group_size: int = 1

    @property
    def proposed_node_id(self) -> Optional[str]:
        """The top-ranked candidate, or None when nothing fit."""
        return self.candidates[0].node_id if self.candidates else None

    def to_dict(self) -> dict[str, Any]:
        """Return the proposal as a plain dict."""
        return asdict(self)


def _rank_from_parsed(
    parsed: dict[str, Any], arch: Architecture, shortlist_size: int
) -> list[ProposedNode]:
    """Turn a judge response's candidate list into ranked nodes.

    Two kinds of candidate are dropped rather than trusted:

    * **Unknown node ids.** A hallucinated id would otherwise become a proposal
      a human might accept.
    * **Candidates the judge's own note rejects.** A note reading "a poor fit,
      these are external competitor tools" attached to a ranked node is not a
      ranking — it is an argument against the node, and shipping it as rank 1
      produces a confident wrong filing. The judge marks these ``fit: poor``;
      when it writes the rejection in prose and marks the candidate ``fits``
      anyway, ``_note_rejects`` catches it.

    Dropping every candidate is a real and correct outcome: it leaves an empty
    shortlist, which ``propose`` reports as NO_PROPOSAL and the pipeline shows
    as "no match" rather than as a node to confirm.

    Args:
        parsed: The parsed judge response.
        arch: A loaded Architecture.
        shortlist_size: Maximum candidates to keep.

    Returns:
        Ranked nodes, best first. Possibly empty.
    """
    ranked: list[ProposedNode] = []
    for item in parsed.get("candidates", []):
        if not isinstance(item, dict):
            continue
        node_id = str(item.get("node_id", "")).strip()
        node = arch.nodes.get(node_id)
        if node is None:
            logger.warning(
                "[ClassifierV3] judge proposed unknown node %r, dropped", node_id[:60]
            )
            continue

        note = str(item.get("note", "")).strip()
        fit = str(item.get("fit", "")).strip().lower()
        if fit and fit not in (FIT_OK, FIT_POOR):
            # An unrecognised verdict is not read as a rejection — that would
            # let one stray word empty a good shortlist. The note still decides.
            logger.warning(
                "[ClassifierV3] unrecognised fit %r on %s, judging by the note",
                fit[:40],
                node_id,
            )
            fit = ""

        if fit == FIT_POOR or _note_rejects(note):
            logger.warning(
                "[ClassifierV3] dropped %s: %s | note: %r",
                node_id,
                (
                    "judge marked the fit poor"
                    if fit == FIT_POOR
                    else f"note rejects its own node (fit={fit or 'absent'})"
                ),
                note[:160],
            )
            continue

        section_level = is_section(arch, node_id)
        ranked.append(
            ProposedNode(
                rank=len(ranked) + 1,
                node_id=node_id,
                title=node.get("node_title"),
                # A section IS its own section; only a leaf has a parent above it.
                section_id=node_id if section_level else parent_of(node_id),
                note=note,
                degraded=bool(node.get("degraded_target")),
                degraded_reason=node.get("degraded_reason"),
                level="section" if section_level else "leaf",
            )
        )
        if len(ranked) >= shortlist_size:
            break
    return ranked


def _resolve_subsumed_by(
    parsed: dict[str, Any], siblings: Optional[Sequence[str]]
) -> Optional[str]:
    """Resolve a judge's ``subsumed_by`` label to the sibling fact it names.

    Accepts "S2", "2", or the sibling's text. Returns None when the field is
    absent, null, or does not resolve — an unresolvable label must not silently
    drop a fact, so the caller treats it as "not subsumed".

    Args:
        parsed: The parsed judge response.
        siblings: The sibling facts passed into this call, in label order.

    Returns:
        The subsuming sibling's text, or None.
    """
    raw = parsed.get("subsumed_by")
    if raw is None or not siblings:
        return None
    label = str(raw).strip()
    if not label or label.lower() in ("null", "none", "false", ""):
        return None

    digits = label.lstrip("Ss").strip(". ")
    if digits.isdigit():
        position = int(digits) - 1
        if 0 <= position < len(siblings):
            return siblings[position]
        logger.warning(
            "[ClassifierV3] subsumed_by %r is out of range for %d siblings",
            label,
            len(siblings),
        )
        return None

    for sibling in siblings:
        if label == sibling or label in sibling:
            return sibling

    logger.warning("[ClassifierV3] could not resolve subsumed_by %r", label[:80])
    return None


def propose(
    fact: str,
    arch: Architecture,
    fact_vector: Optional[list[float]] = None,
    sections_to_consider: int = 3,
    shortlist_size: int = SHORTLIST_SIZE,
    model_id: Optional[str] = None,
    siblings: Optional[Sequence[str]] = None,
    passage: Optional[str] = None,
) -> Proposal:
    """Produce a ranked shortlist for one fact. Files nothing, decides nothing.

    This replaces ``classify()`` in the pipeline. ``classify()`` stays as the
    measurement primitive the eval harness scores; it is no longer a filing path.

    Why a shortlist rather than a pick: measured leaf recall@10 is 57.4% at
    best, so committing to one node is wrong roughly half the time, while the
    correct SECTION is in the top 5 about 70% of the time. Handing a human the
    right neighbourhood is a job retrieval can actually do.

    Degraded nodes stay in the shortlist and are flagged rather than removed.
    Hiding them does not protect a fact — it pushes it toward a wrong-but-trusted
    node, the same reason ``trusted_only`` defaults to FALSE. Nothing auto-files
    here, so the old "never auto-file a degraded target" gate has no decision
    left to guard: the flag now travels to the human instead.

    Args:
        fact: The atomic fact text.
        arch: A loaded Architecture.
        fact_vector: Pre-computed embedding, to avoid re-embedding in batches.
        sections_to_consider: How many sections' leaves the judge sees.
        shortlist_size: Maximum candidates returned to the human.
        model_id: Bedrock model id. Defaults to CLAUDE_SONNET_MODEL.
        siblings: Adjacent facts from the same document, as context. Enables the
            two sibling-dependent outcomes: SUBSUMED (this fact is a strict
            partial of a neighbour and should not be filed) and primary-claim
            selection on a fact that merges a main and a subordinate clause.
        passage: The source text surrounding the segment. Facts are stored
            verbatim, so a segment may be "It replaced the per-seat model" with
            the referent in the sentence before. The passage is how the judge
            resolves that without anything being rewritten — comprehension here,
            storage untouched. Retrieval still embeds the segment alone.

    Returns:
        A Proposal carrying the ranked candidates and the sections behind them,
        or decision=SUBSUMED with no candidates when a neighbour contains it.
    """
    if fact_vector is None:
        from services.embedding_service import embed

        fact_vector = embed(fact, input_type="search_query")

    model = model_id or os.getenv(DEFAULT_MODEL_ENV, DEFAULT_MODEL)
    target = configured_pool_size()
    top_n = MAX_POOL_SECTIONS if target else max(sections_to_consider, 2)
    sections = rank_sections(fact_vector, arch, top_n=top_n, fact_text=fact)
    margin = (
        round(sections[0].best_leaf_similarity - sections[1].best_leaf_similarity, 4)
        if len(sections) > 1
        else None
    )
    chosen = (
        _sections_for_target(arch, sections, target)
        if target
        else sections[:sections_to_consider]
    )

    candidate_ids = candidate_pool(arch, chosen)

    if not candidate_ids:
        return Proposal(
            fact=fact,
            decision=NO_PROPOSAL,
            candidates=[],
            sections=sections,
            section_margin=margin,
            considered_leaf_ids=[],
            reason="retrieval surfaced no candidate leaves",
        )

    raw = _call_llm(
        RANK_PROMPT,
        _build_judge_input(fact, candidate_ids, arch, siblings, passage),
        model,
    )
    parsed = _parse_json_object(raw)

    subsumed_by = _resolve_subsumed_by(parsed, siblings)
    if subsumed_by is not None:
        logger.info(
            "[ClassifierV3] %r is subsumed by %r — not filed",
            fact[:60],
            subsumed_by[:60],
        )
        return Proposal(
            fact=fact,
            decision=SUBSUMED,
            candidates=[],
            sections=sections,
            section_margin=margin,
            considered_leaf_ids=candidate_ids,
            reason=str(parsed.get("reason", "")).strip()
            or "fully contained in an adjacent fact",
            subsumed_by=subsumed_by,
        )

    ranked = _rank_from_parsed(parsed, arch, shortlist_size)

    return Proposal(
        fact=fact,
        decision=PROPOSED if ranked else NO_PROPOSAL,
        candidates=ranked,
        sections=sections,
        section_margin=margin,
        considered_leaf_ids=candidate_ids,
        reason=str(parsed.get("reason", "")).strip()
        or (
            "no candidate fit — the human should browse the tree" if not ranked else ""
        ),
    )


def propose_group(
    facts: Sequence[str],
    arch: Architecture,
    group_id: Optional[str] = None,
    group_vector: Optional[list[float]] = None,
    sections_to_consider: int = 3,
    shortlist_size: int = SHORTLIST_SIZE,
    model_id: Optional[str] = None,
    passage: Optional[str] = None,
) -> Proposal:
    """Produce ONE shortlist for a whole list of facts the chunker grouped.

    A pricing table split into three atomic facts is one filing decision, not
    three. Classified independently the members diverge — measured on the
    reference run, three pricing tiers from one sentence landed on two different
    nodes and three cost line items on two others — and that divergence is
    indistinguishable downstream from the source actually disagreeing with
    itself.

    Costs one embedding and one judge call for the whole group instead of one of
    each per member, so grouping REDUCES calls.

    Args:
        facts: Every member of the group, in source order. Must be non-empty.
        arch: A loaded Architecture.
        group_id: The group's id, recorded on the returned Proposal.
        group_vector: Pre-computed embedding of the joined members.
        sections_to_consider: How many sections' leaves the judge sees.
        shortlist_size: Maximum candidates returned to the human.
        model_id: Bedrock model id. Defaults to CLAUDE_SONNET_MODEL.
        passage: The source text around the list. Members are verbatim segments,
            so the frame they depend on ("all figures are per institution per
            year") often sits in the passage rather than in any member.

    Returns:
        One Proposal, to be applied to every member of the group. ``fact`` holds
        the joined members, since the proposal describes the list, not one member.

    Raises:
        ValueError: ``facts`` is empty.
    """
    if not facts:
        raise ValueError("propose_group requires at least one fact")

    joined = " ".join(facts)
    if group_vector is None:
        from services.embedding_service import embed

        group_vector = embed(joined, input_type="search_query")

    model = model_id or os.getenv(DEFAULT_MODEL_ENV, DEFAULT_MODEL)
    sections = rank_sections(
        group_vector,
        arch,
        top_n=max(sections_to_consider, 2),
        fact_text=joined,
    )
    margin = (
        round(sections[0].best_leaf_similarity - sections[1].best_leaf_similarity, 4)
        if len(sections) > 1
        else None
    )
    chosen = sections[:sections_to_consider]

    candidate_ids = candidate_pool(arch, chosen)

    if not candidate_ids:
        return Proposal(
            fact=joined,
            decision=NO_PROPOSAL,
            candidates=[],
            sections=sections,
            section_margin=margin,
            considered_leaf_ids=[],
            reason="retrieval surfaced no candidate leaves",
            group_id=group_id,
            group_size=len(facts),
        )

    raw = _call_llm(
        GROUP_RANK_PROMPT,
        _build_group_input(facts, candidate_ids, arch, passage),
        model,
    )
    parsed = _parse_json_object(raw)
    ranked = _rank_from_parsed(parsed, arch, shortlist_size)

    logger.info(
        "[ClassifierV3] group %s (%d facts) -> %s",
        group_id or "?",
        len(facts),
        ranked[0].node_id if ranked else "no proposal",
    )
    return Proposal(
        fact=joined,
        decision=PROPOSED if ranked else NO_PROPOSAL,
        candidates=ranked,
        sections=sections,
        section_margin=margin,
        considered_leaf_ids=candidate_ids,
        reason=str(parsed.get("reason", "")).strip()
        or (
            "no candidate fit — the human should browse the tree" if not ranked else ""
        ),
        group_id=group_id,
        group_size=len(facts),
    )


def classify(
    fact: str,
    arch: Architecture,
    fact_vector: Optional[list[float]] = None,
    sections_to_consider: int = 1,
    section_floor: float = PROVISIONAL_SECTION_FLOOR,
    model_id: Optional[str] = None,
    margin_gate: Optional[float] = None,
    skip_judge_single_sibling: bool = False,
) -> RoutingV3:
    """Route one fact: section-first retrieval, then a judge over all siblings.

    Args:
        fact: The atomic fact text.
        arch: A loaded Architecture.
        fact_vector: Pre-computed embedding, to avoid re-embedding in batch runs.
        sections_to_consider: 1 commits to the top section; 2 judges across both
            sibling sets, which is the recovery path for section misses.
        section_floor: PROVISIONAL. Below this best-leaf similarity the section
            retrieval is treated as low-confidence and the fact goes to review
            without consulting the judge.
        model_id: Bedrock model id. Defaults to CLAUDE_SONNET_MODEL.
        margin_gate: When set, overrides sections_to_consider per fact — commit
            to the top section when the section margin is at least this wide,
            and widen to two sections when it is narrower. Saves nothing in call
            count (it is one judge call either way); it trades prompt size for a
            wider candidate set only where the section choice is genuinely close.
        skip_judge_single_sibling: When the chosen section holds exactly one
            leaf, file to it without calling the judge.

            ⚠️ This is NOT a free optimisation. The judge does two jobs: pick
            among siblings, and refuse ("none fit") when the fact belongs
            nowhere in the set. Only the first is impossible with one sibling.
            Skipping the call therefore converts every possible refusal into a
            forced auto-file, and section retrieval is right well under half the
            time. Measure before enabling — see the OPT-1 row in
            scripts/eval_classifier_v3.py.

    Returns:
        A RoutingV3 carrying the candidates, the judge's reasoning, and the route.
    """
    if fact_vector is None:
        from services.embedding_service import embed

        fact_vector = embed(fact, input_type="search_query")

    model = model_id or os.getenv(DEFAULT_MODEL_ENV, DEFAULT_MODEL)

    sections = rank_sections(fact_vector, arch, top_n=2, fact_text=fact)
    margin = (
        round(sections[0].best_leaf_similarity - sections[1].best_leaf_similarity, 4)
        if len(sections) > 1
        else None
    )

    if margin_gate is not None:
        sections_to_consider = 1 if (margin or 0.0) >= margin_gate else 2

    thresholds = {
        "section_floor": section_floor,
        "sections_to_consider": float(sections_to_consider),
        "margin_gate": -1.0 if margin_gate is None else margin_gate,
    }
    chosen = sections[:sections_to_consider]

    base = dict(
        fact=fact,
        sections=sections,
        section_margin=margin,
        thresholds_provisional=thresholds,
    )

    if not chosen:
        return RoutingV3(
            **base,
            candidate_leaf_ids=[],
            judge_choice=None,
            judge_confidence=None,
            judge_reason="",
            decision=REVIEW,
            target_node_id=REVIEW_BUCKET,
            reason="section retrieval returned nothing",
        )

    if chosen[0].best_leaf_similarity < section_floor:
        return RoutingV3(
            **base,
            candidate_leaf_ids=[],
            judge_choice=None,
            judge_confidence=None,
            judge_reason="",
            decision=REVIEW,
            target_node_id=REVIEW_BUCKET,
            reason=(
                f"section retrieval low-confidence: best leaf similarity "
                f"{chosen[0].best_leaf_similarity} < {section_floor} (provisional)"
            ),
        )

    candidate_ids: list[str] = []
    for section in chosen:
        candidate_ids += arch.siblings.get(section.section_id, [])

    base.update(candidate_leaf_ids=candidate_ids)

    if skip_judge_single_sibling and len(candidate_ids) == 1:
        only = candidate_ids[0]
        node = arch.nodes[only]
        if node.get("degraded_target"):
            return RoutingV3(
                **base,
                judge_choice=only,
                judge_confidence=None,
                judge_reason="",
                decision=REVIEW,
                target_node_id=REVIEW_BUCKET,
                leaf_degraded=True,
                leaf_degraded_reason=node.get("degraded_reason"),
                reason=(
                    f"single-sibling section, but {only} is a degraded target "
                    f"({node.get('degraded_reason')}) — never auto-filed"
                ),
            )
        return RoutingV3(
            **base,
            judge_choice=only,
            judge_confidence="high",
            judge_reason="",
            decision=AUTO_FILE,
            target_node_id=only,
            reason=(
                f"single-sibling section {chosen[0].section_id}: judge skipped, "
                f"filed to {only} without a fit check"
            ),
        )

    raw = _call_llm(JUDGE_PROMPT, _build_judge_input(fact, candidate_ids, arch), model)
    parsed = _parse_json_object(raw)
    choice = str(parsed.get("choice", NONE_FIT)).strip()
    confidence = str(parsed.get("confidence", "low")).strip().lower()
    judge_reason = str(parsed.get("reason", "")).strip()

    base.update(
        judge_choice=choice,
        judge_confidence=confidence,
        judge_reason=judge_reason,
    )

    if choice == NONE_FIT or choice not in arch.nodes:
        if choice != NONE_FIT:
            logger.warning(
                "[ClassifierV3] judge returned unknown node id %r, routing to review",
                choice[:60],
            )
        return RoutingV3(
            **base,
            decision=REVIEW,
            target_node_id=REVIEW_BUCKET,
            reason="judge found no fitting node in the candidate set",
        )

    node = arch.nodes[choice]

    # The contract: a degraded target is never auto-filed, however confident.
    if node.get("degraded_target"):
        return RoutingV3(
            **base,
            decision=REVIEW,
            target_node_id=REVIEW_BUCKET,
            leaf_degraded=True,
            leaf_degraded_reason=node.get("degraded_reason"),
            reason=(
                f"judge picked {choice}, which is a degraded target "
                f"({node.get('degraded_reason')}) — held for review, never auto-filed"
            ),
        )

    if confidence == "high":
        return RoutingV3(
            **base,
            decision=AUTO_FILE,
            target_node_id=choice,
            reason=f"judge picked {choice} with high confidence",
        )

    return RoutingV3(
        **base,
        decision=PARENT_PARKED,
        target_node_id=parent_of(choice),
        parent_parked=True,
        reason=(
            f"judge leaned to {choice} but was not confident — parked at "
            f"{parent_of(choice)} for Build to resolve"
        ),
    )
