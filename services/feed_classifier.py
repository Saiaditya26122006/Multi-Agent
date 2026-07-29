"""Feed classifier v1 — hierarchical, embedding-only routing of a fact to a node.

Takes one atomic fact and walks the architecture tree top-down:

    domain (BP.X, 12)  ->  section (BP.X.Y, 81)  ->  leaf (BP.X.Y.Z+, 819)

At each level it scores the fact's embedding against that level's candidates by
cosine similarity and descends into the winner. It never scores a fact against
all 819 leaves at once.

Deliberately simple: embeddings only, no LLM judge, no reranking, no keyword
fallback. The point of v1 is a real accuracy number to set thresholds from.

Two rules are not simplifications and must survive any later version:

* **Search sees everything; the gate decides.** Degraded leaves stay in the
  candidate pool. Filtering them out of the *search* does not protect a fact —
  it silently redirects it to a wrong-but-trusted node. See the degraded_target
  CONTRACT at the top of PROJECT_STATE.md.
* **A degraded leaf is never auto-filed.** If the best leaf is degraded, the
  fact routes to BP.13 review carrying the node id and degraded_reason.

Standalone: reads bp_architecture, writes nothing.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)

# PROVISIONAL — placeholders so v1 produces a decision at all. These are NOT
# tuned values; the measurement sets them. Do not treat any number here as
# meaningful until the threshold table exists.
PROVISIONAL_LEAF_THRESHOLD = 0.5
PROVISIONAL_SECTION_THRESHOLD = 0.5
PROVISIONAL_DOMAIN_THRESHOLD = 0.5

REVIEW_BUCKET = "BP.13"

AUTO_FILE = "auto_file"
PARENT_PARKED = "parent_parked"
REVIEW = "review"


@dataclass
class LevelMatch:
    """Best candidate at one level of the tree."""

    node_id: Optional[str]
    title: Optional[str]
    confidence: Optional[float]
    runner_up_id: Optional[str] = None
    runner_up_confidence: Optional[float] = None

    @property
    def margin(self) -> Optional[float]:
        """Gap between the winner and the runner-up, or None."""
        if self.confidence is None or self.runner_up_confidence is None:
            return None
        return round(self.confidence - self.runner_up_confidence, 4)


@dataclass
class Routing:
    """The routing decision for one fact, with confidences at every level."""

    fact: str
    domain: LevelMatch
    section: LevelMatch
    leaf: LevelMatch
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


class ArchitectureIndex:
    """In-memory embedding index over bp_architecture, grouped by tree level.

    Built once and reused across facts — loading 904 vectors over PostgREST
    takes ~15s, which must not be paid per fact.
    """

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.by_id: dict[str, dict[str, Any]] = {}
        domains, sections, leaves = [], [], []

        for row in rows:
            if row.get("embedding") is None:
                continue
            vec = row["embedding"]
            row["_vec"] = np.asarray(
                json.loads(vec) if isinstance(vec, str) else vec, dtype=np.float32
            )
            self.by_id[row["node_id"]] = row
            depth = row["node_id"].count(".")
            if depth == 1:
                domains.append(row)
            elif depth == 2:
                sections.append(row)
            else:
                leaves.append(row)

        self.domains = self._pack(domains)
        self.sections_by_domain: dict[str, Any] = {}
        for row in sections:
            self.sections_by_domain.setdefault(_domain_of(row["node_id"]), []).append(row)
        self.sections_by_domain = {
            k: self._pack(v) for k, v in self.sections_by_domain.items()
        }

        self.leaves_by_section: dict[str, Any] = {}
        for row in leaves:
            self.leaves_by_section.setdefault(_section_of(row["node_id"]), []).append(row)
        self.leaves_by_section = {
            k: self._pack(v) for k, v in self.leaves_by_section.items()
        }

        logger.info(
            "[Classifier] index: %d domains, %d sections, %d leaves",
            len(domains),
            len(sections),
            len(leaves),
        )

    @staticmethod
    def _pack(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], np.ndarray]:
        """Stack a group's vectors into one L2-normalised matrix for fast scoring."""
        if not rows:
            return [], np.zeros((0, 1), dtype=np.float32)
        mat = np.vstack([r["_vec"] for r in rows])
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return rows, mat / norms


def _domain_of(node_id: str) -> str:
    """Return the BP.X domain prefix of a node id."""
    return ".".join(node_id.split(".")[:2])


def _section_of(node_id: str) -> str:
    """Return the BP.X.Y section prefix of a node id."""
    return ".".join(node_id.split(".")[:3])


def load_index(supabase_client: Any) -> ArchitectureIndex:
    """Load every embedded bp_architecture row into an ArchitectureIndex."""
    rows: list[dict[str, Any]] = []
    start = 0
    while True:
        resp = (
            supabase_client.table("bp_architecture")
            .select("node_id,node_title,degraded_target,degraded_reason,embedding")
            .not_.is_("embedding", "null")
            .range(start, start + 99)
            .execute()
        )
        if not resp.data:
            break
        rows += resp.data
        start += 100
    return ArchitectureIndex(rows)


def _best(
    query: np.ndarray, packed: tuple[list[dict[str, Any]], np.ndarray]
) -> LevelMatch:
    """Score a query vector against one packed candidate group."""
    rows, mat = packed
    if not rows:
        return LevelMatch(None, None, None)
    sims = mat @ query
    order = np.argsort(-sims)
    top = rows[int(order[0])]
    runner = rows[int(order[1])] if len(order) > 1 else None
    return LevelMatch(
        node_id=top["node_id"],
        title=top["node_title"],
        confidence=round(float(sims[order[0]]), 4),
        runner_up_id=runner["node_id"] if runner else None,
        runner_up_confidence=round(float(sims[order[1]]), 4) if runner else None,
    )


def classify(
    fact: str,
    index: ArchitectureIndex,
    fact_vector: Optional[np.ndarray] = None,
    leaf_threshold: float = PROVISIONAL_LEAF_THRESHOLD,
    section_threshold: float = PROVISIONAL_SECTION_THRESHOLD,
    domain_threshold: float = PROVISIONAL_DOMAIN_THRESHOLD,
) -> Routing:
    """Route one fact to a node by walking domain -> section -> leaf.

    Args:
        fact: The atomic fact text.
        index: A loaded ArchitectureIndex.
        fact_vector: Pre-computed embedding, to avoid re-embedding in batch runs.
        leaf_threshold: PROVISIONAL. Set from measurement, not from intuition.
        section_threshold: PROVISIONAL.
        domain_threshold: PROVISIONAL.

    Returns:
        A Routing carrying the confidence at every level and the decision.
    """
    if fact_vector is None:
        from services.embedding_service import embed

        fact_vector = np.asarray(embed(fact, input_type="search_query"), dtype=np.float32)
    norm = np.linalg.norm(fact_vector)
    query = fact_vector / (norm if norm else 1.0)

    domain = _best(query, index.domains)
    section = LevelMatch(None, None, None)
    leaf = LevelMatch(None, None, None)

    if domain.node_id:
        section = _best(query, index.sections_by_domain.get(domain.node_id, ([], None)))
    if section.node_id:
        leaf = _best(query, index.leaves_by_section.get(section.node_id, ([], None)))

    leaf_row = index.by_id.get(leaf.node_id) if leaf.node_id else None
    leaf_degraded = bool(leaf_row and leaf_row["degraded_target"])
    leaf_reason = leaf_row["degraded_reason"] if leaf_degraded else None

    thresholds = {
        "leaf": leaf_threshold,
        "section": section_threshold,
        "domain": domain_threshold,
    }

    # The contract: a degraded leaf is never auto-filed, however confident.
    if leaf_degraded:
        return Routing(
            fact=fact,
            domain=domain,
            section=section,
            leaf=leaf,
            decision=REVIEW,
            target_node_id=REVIEW_BUCKET,
            leaf_degraded=True,
            leaf_degraded_reason=leaf_reason,
            reason=(
                f"best leaf {leaf.node_id} is a degraded target "
                f"({leaf_reason}) — held for review, never auto-filed"
            ),
            thresholds_provisional=thresholds,
        )

    if leaf.confidence is not None and leaf.confidence >= leaf_threshold:
        return Routing(
            fact=fact,
            domain=domain,
            section=section,
            leaf=leaf,
            decision=AUTO_FILE,
            target_node_id=leaf.node_id,
            reason=f"leaf confidence {leaf.confidence} >= {leaf_threshold} (provisional)",
            thresholds_provisional=thresholds,
        )

    if section.confidence is not None and section.confidence >= section_threshold:
        return Routing(
            fact=fact,
            domain=domain,
            section=section,
            leaf=leaf,
            decision=PARENT_PARKED,
            target_node_id=section.node_id,
            parent_parked=True,
            reason=(
                f"section confidence {section.confidence} >= {section_threshold} but "
                f"leaf confidence {leaf.confidence} < {leaf_threshold} (provisional) — "
                "parked at the parent for Build to resolve to a leaf"
            ),
            thresholds_provisional=thresholds,
        )

    return Routing(
        fact=fact,
        domain=domain,
        section=section,
        leaf=leaf,
        decision=REVIEW,
        target_node_id=REVIEW_BUCKET,
        reason=(
            f"no level cleared its provisional threshold "
            f"(domain={domain.confidence}, section={section.confidence}, "
            f"leaf={leaf.confidence})"
        ),
        thresholds_provisional=thresholds,
    )


def classify_batch(
    facts: list[str], index: ArchitectureIndex, **kwargs: Any
) -> list[Routing]:
    """Classify many facts, embedding each once. Order is preserved."""
    from services.embedding_service import embed

    out: list[Routing] = []
    for i, fact in enumerate(facts, 1):
        vec = np.asarray(embed(fact, input_type="search_query"), dtype=np.float32)
        out.append(classify(fact, index, fact_vector=vec, **kwargs))
        if i % 25 == 0:
            logger.info("[Classifier] %d/%d facts classified", i, len(facts))
    return out
