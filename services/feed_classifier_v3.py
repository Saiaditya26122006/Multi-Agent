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
from typing import Any, Optional

import boto3
import numpy as np

logger = logging.getLogger(__name__)

_bedrock_client = None

DEFAULT_MODEL_ENV = "CLAUDE_SONNET_MODEL"
DEFAULT_MODEL = "us.anthropic.claude-sonnet-4-6"
MAX_TOKENS = 2048
MAX_RETRIES = 3
RETRY_BACKOFF = (1, 3, 8)

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
then justify it."""


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

=== OUTPUT ===
Return one JSON object, nothing else:

{"reason": "<one sentence: what the fact actually asserts>",
 "candidates": [{"node_id": "<id>", "note": "<one sentence to the human>"}, ...]}

Write "reason" first and let the ranking follow from it."""


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
    raise RuntimeError(f"Bedrock call failed after {MAX_RETRIES} attempts") from last_error


def _parse_json_object(raw: str) -> dict[str, Any]:
    """Parse a JSON object from a model response, tolerating markdown fences."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1] if "```" in cleaned[3:] else cleaned[3:]
        if cleaned.lstrip().startswith("json"):
            cleaned = cleaned.lstrip()[4:]
    cleaned = cleaned.strip().strip("`").strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.warning("[ClassifierV3] JSON parse failed (%s), trying repair", exc)

    try:
        from json_repair import repair_json

        return json.loads(repair_json(cleaned))
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


def load_architecture(supabase_client: Any = None) -> Architecture:
    """Load every node, build the childless-leaf matrix and the sibling index.

    A "leaf" is a node with no children, not a node at depth >= 3. BP.11.7 sits
    at depth 2 with zero children and is a legitimate filing target.
    """
    supabase_client = supabase_client or get_architecture_client()
    rows: list[dict[str, Any]] = []
    start = 0
    while True:
        resp = (
            supabase_client.table("bp_architecture")
            .select(ARCH_COLUMNS)
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
                json.loads(r["embedding"])
                if isinstance(r["embedding"], str)
                else r["embedding"],
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
    fact_vector: list[float], arch: Architecture, top_n: int = 2
) -> list[SectionCandidate]:
    """Rank sections by induced similarity: best leaf in each section wins.

    Direct scoring against section embeddings measured 7.4% @1 and is not used.
    """
    q = np.asarray(fact_vector, dtype=np.float32)
    q /= np.linalg.norm(q)
    sims = arch.leaf_matrix @ q
    order = np.argsort(-sims)[:INDUCE_DEPTH]

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


def _build_judge_input(fact: str, candidate_ids: list[str], arch: Architecture) -> str:
    """Compose the judge's user message: the fact plus every candidate node."""
    blocks = [_describe(arch.nodes[n]) for n in candidate_ids]
    return (
        f"FACT TO FILE:\n{fact}\n\n"
        f"CANDIDATE NODES ({len(candidate_ids)}):\n\n"
        + "\n---\n".join(blocks)
    )


PROPOSED = "proposed"
NO_PROPOSAL = "no_proposal"
SHORTLIST_SIZE = 5


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

    @property
    def proposed_node_id(self) -> Optional[str]:
        """The top-ranked candidate, or None when nothing fit."""
        return self.candidates[0].node_id if self.candidates else None

    def to_dict(self) -> dict[str, Any]:
        """Return the proposal as a plain dict."""
        return asdict(self)


def propose(
    fact: str,
    arch: Architecture,
    fact_vector: Optional[list[float]] = None,
    sections_to_consider: int = 3,
    shortlist_size: int = SHORTLIST_SIZE,
    model_id: Optional[str] = None,
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

    Returns:
        A Proposal carrying the ranked candidates and the sections behind them.
    """
    if fact_vector is None:
        from services.embedding_service import embed

        fact_vector = embed(fact, input_type="search_query")

    model = model_id or os.getenv(DEFAULT_MODEL_ENV, DEFAULT_MODEL)
    sections = rank_sections(fact_vector, arch, top_n=max(sections_to_consider, 2))
    margin = (
        round(sections[0].best_leaf_similarity - sections[1].best_leaf_similarity, 4)
        if len(sections) > 1
        else None
    )
    chosen = sections[:sections_to_consider]

    candidate_ids: list[str] = []
    for section in chosen:
        candidate_ids += arch.siblings.get(section.section_id, [])

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

    raw = _call_llm(RANK_PROMPT, _build_judge_input(fact, candidate_ids, arch), model)
    parsed = _parse_json_object(raw)

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
        ranked.append(
            ProposedNode(
                rank=len(ranked) + 1,
                node_id=node_id,
                title=node.get("node_title"),
                section_id=parent_of(node_id),
                note=str(item.get("note", "")).strip(),
                degraded=bool(node.get("degraded_target")),
                degraded_reason=node.get("degraded_reason"),
            )
        )
        if len(ranked) >= shortlist_size:
            break

    return Proposal(
        fact=fact,
        decision=PROPOSED if ranked else NO_PROPOSAL,
        candidates=ranked,
        sections=sections,
        section_margin=margin,
        considered_leaf_ids=candidate_ids,
        reason=str(parsed.get("reason", "")).strip()
        or ("no candidate fit — the human should browse the tree" if not ranked else ""),
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

    sections = rank_sections(fact_vector, arch, top_n=2)
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
