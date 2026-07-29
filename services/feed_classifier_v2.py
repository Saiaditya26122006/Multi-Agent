"""Feed classifier v2 — flat retrieval over leaves, tree used only as context.

v1 walked domain -> section -> leaf and lost 40 of 54 facts at the first hop:
domain embeddings are abstract governance text and do not discriminate (total
spread across all 12 domains was 0.22, with winners decided by margins of 0.03).
A near-tie at hop one discards 80% of the tree irrecoverably.

v2 removes the navigation gate entirely:

    embed fact -> HNSW top-k over ALL leaves -> route on the candidates

The tree is still used, but only as *evidence*: agreement among the top-k about
which section they belong to is a sanity signal, never a filter applied before
retrieval.

Two rules carried forward unchanged:

* **Retrieval sees degraded nodes; the gate decides.** ``match_bp_architecture``
  is called with ``trusted_only=false`` (its default, for exactly this reason).
  Filtering degraded rows out of the search does not protect a fact — it
  redirects it to a wrong-but-trusted node. See the degraded_target CONTRACT at
  the top of PROJECT_STATE.md.
* **A degraded leaf is never auto-filed.** If the best leaf is degraded, the fact
  routes to BP.13 review carrying the node id and degraded_reason.

A "leaf" is a node with **no children**, not a node at depth >= 3. BP.11.7 sits
at depth 2 with zero children and is a legitimate filing target; a depth rule
would make it unreachable.

Standalone: reads bp_architecture, writes nothing.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

# PROVISIONAL placeholders so v2 produces a decision at all. The measurement
# sets these; nothing here is tuned.
PROVISIONAL_LEAF_THRESHOLD = 0.5
PROVISIONAL_SECTION_THRESHOLD = 0.5

REVIEW_BUCKET = "BP.13"
AUTO_FILE = "auto_file"
PARENT_PARKED = "parent_parked"
REVIEW = "review"

RETRIEVE_K = 10
RPC_FETCH = 60  # over-fetch: the RPC also returns domains and sections


@dataclass
class Candidate:
    """One retrieved leaf and its similarity to the fact."""

    node_id: str
    title: Optional[str]
    similarity: float
    degraded: bool
    degraded_reason: Optional[str]


@dataclass
class RoutingV2:
    """The routing decision for one fact, with the evidence behind it."""

    fact: str
    candidates: list[Candidate]
    top_id: Optional[str]
    top_title: Optional[str]
    top_confidence: Optional[float]
    margin: Optional[float]
    section_agreement: Optional[float]
    agreed_section: Optional[str]
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


def section_of(node_id: str) -> str:
    """Return the BP.X.Y section prefix of a node id."""
    return ".".join(node_id.split(".")[:3])


def load_leaf_ids(supabase_client: Any) -> set[str]:
    """Return every node id that has no children.

    A childless node is a filing target regardless of its depth.
    """
    ids: list[str] = []
    start = 0
    while True:
        resp = (
            supabase_client.table("bp_architecture")
            .select("node_id")
            .range(start, start + 999)
            .execute()
        )
        if not resp.data:
            break
        ids += [r["node_id"] for r in resp.data]
        start += 1000

    parents = {".".join(i.split(".")[:-1]) for i in ids}
    leaves = {i for i in ids if i not in parents}
    logger.info("[ClassifierV2] %d nodes, %d childless leaves", len(ids), len(leaves))
    return leaves


def classify(
    fact: str,
    supabase_client: Any,
    leaf_ids: set[str],
    fact_vector: Optional[list[float]] = None,
    k: int = RETRIEVE_K,
    leaf_threshold: float = PROVISIONAL_LEAF_THRESHOLD,
    section_threshold: float = PROVISIONAL_SECTION_THRESHOLD,
) -> RoutingV2:
    """Route one fact by flat HNSW retrieval over all leaves.

    Args:
        fact: The atomic fact text.
        supabase_client: A Supabase client (service role).
        leaf_ids: Childless node ids, from load_leaf_ids.
        fact_vector: Pre-computed embedding, to avoid re-embedding in batch runs.
        k: How many leaf candidates to keep.
        leaf_threshold: PROVISIONAL.
        section_threshold: PROVISIONAL.

    Returns:
        A RoutingV2 carrying the candidates, the decision, and why.
    """
    if fact_vector is None:
        from services.embedding_service import embed

        fact_vector = embed(fact, input_type="search_query")

    rows = supabase_client.rpc(
        "match_bp_architecture",
        {
            "query_embedding": fact_vector,
            "match_threshold": 0.0,
            "match_count": RPC_FETCH,
            "trusted_only": False,  # the contract: search sees everything
        },
    ).execute().data

    candidates = [
        Candidate(
            node_id=r["node_id"],
            title=r["node_title"],
            similarity=round(float(r["similarity"]), 4),
            degraded=bool(r["degraded_target"]),
            degraded_reason=r.get("degraded_reason"),
        )
        for r in rows
        if r["node_id"] in leaf_ids
    ][:k]

    thresholds = {"leaf": leaf_threshold, "section": section_threshold}

    if not candidates:
        return RoutingV2(
            fact=fact,
            candidates=[],
            top_id=None,
            top_title=None,
            top_confidence=None,
            margin=None,
            section_agreement=None,
            agreed_section=None,
            decision=REVIEW,
            target_node_id=REVIEW_BUCKET,
            reason="retrieval returned no leaf candidates",
            thresholds_provisional=thresholds,
        )

    top = candidates[0]
    margin = (
        round(top.similarity - candidates[1].similarity, 4)
        if len(candidates) > 1
        else None
    )

    # Tree as evidence, not as a gate: how much do the top-k agree on a section?
    sections = Counter(section_of(c.node_id) for c in candidates)
    agreed_section, agree_n = sections.most_common(1)[0]
    agreement = round(agree_n / len(candidates), 3)

    base = dict(
        fact=fact,
        candidates=candidates,
        top_id=top.node_id,
        top_title=top.title,
        top_confidence=top.similarity,
        margin=margin,
        section_agreement=agreement,
        agreed_section=agreed_section,
        thresholds_provisional=thresholds,
    )

    # The contract: a degraded top match is never auto-filed, however confident.
    if top.degraded:
        return RoutingV2(
            **base,
            decision=REVIEW,
            target_node_id=REVIEW_BUCKET,
            leaf_degraded=True,
            leaf_degraded_reason=top.degraded_reason,
            reason=(
                f"best leaf {top.node_id} is a degraded target "
                f"({top.degraded_reason}) — held for review, never auto-filed"
            ),
        )

    if top.similarity >= leaf_threshold:
        return RoutingV2(
            **base,
            decision=AUTO_FILE,
            target_node_id=top.node_id,
            reason=f"top leaf {top.similarity} >= {leaf_threshold} (provisional)",
        )

    if agreement >= section_threshold:
        return RoutingV2(
            **base,
            decision=PARENT_PARKED,
            target_node_id=agreed_section,
            parent_parked=True,
            reason=(
                f"no leaf cleared {leaf_threshold} (top {top.similarity}) but "
                f"{int(agreement * 100)}% of the top-{len(candidates)} agree on "
                f"{agreed_section} — parked at the parent for Build to resolve"
            ),
        )

    return RoutingV2(
        **base,
        decision=REVIEW,
        target_node_id=REVIEW_BUCKET,
        reason=(
            f"top leaf {top.similarity} < {leaf_threshold} and section agreement "
            f"{agreement} < {section_threshold} (both provisional)"
        ),
    )


def classify_batch(
    facts: list[str], supabase_client: Any, leaf_ids: set[str], **kwargs: Any
) -> list[RoutingV2]:
    """Classify many facts, embedding each once. Order is preserved."""
    from services.embedding_service import embed

    out: list[RoutingV2] = []
    for i, fact in enumerate(facts, 1):
        vec = embed(fact, input_type="search_query")
        out.append(classify(fact, supabase_client, leaf_ids, fact_vector=vec, **kwargs))
        if i % 25 == 0:
            logger.info("[ClassifierV2] %d/%d facts classified", i, len(facts))
    return out
