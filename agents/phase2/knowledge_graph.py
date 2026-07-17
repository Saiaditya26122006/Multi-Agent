"""
Shared Knowledge Graph for inter-agent fact propagation.

Each fact is a node with:
- content: the assertion
- provenance: which agent produced it
- confidence: float 0-1
- dependencies: edges to other facts this depends on
- dependents: edges to facts that depend on this one

When a fact is updated or invalidated, all downstream dependents
are flagged for re-evaluation.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class FactNode:
    id: str
    content: str
    provenance: str
    confidence: float
    section: str
    category: str
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    valid: bool = True
    dependencies: set[str] = field(default_factory=set)
    dependents: set[str] = field(default_factory=set)
    metadata: dict = field(default_factory=dict)


@dataclass
class Hypothesis:
    id: str
    claim: str
    category: str
    evidence_for: list[str] = field(default_factory=list)
    evidence_against: list[str] = field(default_factory=list)
    status: str = "unvalidated"
    confidence: float = 0.5
    owner_agent: str = ""

    @property
    def net_evidence_score(self) -> float:
        total = len(self.evidence_for) + len(self.evidence_against)
        if total == 0:
            return 0.5
        return len(self.evidence_for) / total


class KnowledgeGraph:
    """In-memory knowledge graph for cross-agent fact sharing and propagation."""

    def __init__(self) -> None:
        self._facts: dict[str, FactNode] = {}
        self._hypotheses: dict[str, Hypothesis] = {}
        self._fact_counter: int = 0
        self._hyp_counter: int = 0
        self._invalidation_callbacks: list = []

    # ── Facts ────────────────────────────────────────────────────────────────

    def add_fact(
        self,
        content: str,
        provenance: str,
        confidence: float,
        section: str,
        category: str = "general",
        depends_on: Optional[list[str]] = None,
        metadata: Optional[dict] = None,
    ) -> str:
        """Add a fact node to the graph. Returns fact ID."""
        self._fact_counter += 1
        fact_id = f"fact_{self._fact_counter}"

        node = FactNode(
            id=fact_id,
            content=content,
            provenance=provenance,
            confidence=confidence,
            section=section,
            category=category,
            metadata=metadata or {},
        )

        if depends_on:
            for dep_id in depends_on:
                if dep_id in self._facts:
                    node.dependencies.add(dep_id)
                    self._facts[dep_id].dependents.add(fact_id)

        self._facts[fact_id] = node
        logger.debug("Added fact %s from %s (conf=%.2f)", fact_id, provenance, confidence)
        return fact_id

    def update_fact(self, fact_id: str, new_content: str, new_confidence: float, updater: str) -> list[str]:
        """Update a fact and return IDs of invalidated dependents."""
        if fact_id not in self._facts:
            return []

        node = self._facts[fact_id]
        node.content = new_content
        node.confidence = new_confidence
        node.updated_at = time.time()
        node.provenance = updater

        invalidated = self._propagate_invalidation(fact_id)
        logger.info(
            "Updated fact %s — %d dependents invalidated",
            fact_id, len(invalidated),
        )
        return invalidated

    def invalidate_fact(self, fact_id: str, reason: str = "") -> list[str]:
        """Mark a fact as invalid and cascade to dependents."""
        if fact_id not in self._facts:
            return []

        self._facts[fact_id].valid = False
        self._facts[fact_id].metadata["invalidation_reason"] = reason

        invalidated = self._propagate_invalidation(fact_id)
        logger.info("Invalidated fact %s — %d cascade", fact_id, len(invalidated))
        return invalidated

    def get_fact(self, fact_id: str) -> Optional[FactNode]:
        return self._facts.get(fact_id)

    def get_facts_by_section(self, section: str) -> list[FactNode]:
        return [f for f in self._facts.values() if f.section == section and f.valid]

    def get_facts_by_provenance(self, agent_name: str) -> list[FactNode]:
        return [f for f in self._facts.values() if f.provenance == agent_name and f.valid]

    def get_confidence_ceiling(self, fact_id: str) -> float:
        """Confidence can't exceed the minimum confidence of its dependencies."""
        node = self._facts.get(fact_id)
        if not node or not node.dependencies:
            return 1.0

        min_dep_confidence = 1.0
        for dep_id in node.dependencies:
            dep = self._facts.get(dep_id)
            if dep and dep.valid:
                min_dep_confidence = min(min_dep_confidence, dep.confidence)
            elif dep and not dep.valid:
                return 0.0

        return min_dep_confidence

    def _propagate_invalidation(self, fact_id: str) -> list[str]:
        """BFS invalidation cascade through dependents."""
        invalidated = []
        queue = list(self._facts[fact_id].dependents)
        visited = {fact_id}

        while queue:
            current_id = queue.pop(0)
            if current_id in visited:
                continue
            visited.add(current_id)

            current = self._facts.get(current_id)
            if current and current.valid:
                current.valid = False
                current.metadata["invalidated_by"] = fact_id
                invalidated.append(current_id)
                queue.extend(current.dependents)

        for callback in self._invalidation_callbacks:
            callback(fact_id, invalidated)

        return invalidated

    def on_invalidation(self, callback) -> None:
        """Register a callback for when facts are invalidated."""
        self._invalidation_callbacks.append(callback)

    # ── Hypotheses ───────────────────────────────────────────────────────────

    def add_hypothesis(
        self,
        claim: str,
        category: str,
        owner_agent: str = "",
    ) -> str:
        """Register a hypothesis to track."""
        self._hyp_counter += 1
        hyp_id = f"hyp_{self._hyp_counter}"
        self._hypotheses[hyp_id] = Hypothesis(
            id=hyp_id,
            claim=claim,
            category=category,
            owner_agent=owner_agent,
        )
        logger.debug("Added hypothesis %s: %s", hyp_id, claim[:50])
        return hyp_id

    def add_evidence(self, hypothesis_id: str, fact_id: str, supports: bool) -> None:
        """Link a fact as evidence for or against a hypothesis."""
        hyp = self._hypotheses.get(hypothesis_id)
        if not hyp:
            return

        if supports:
            if fact_id not in hyp.evidence_for:
                hyp.evidence_for.append(fact_id)
        else:
            if fact_id not in hyp.evidence_against:
                hyp.evidence_against.append(fact_id)

        hyp.confidence = hyp.net_evidence_score
        if hyp.confidence >= 0.7:
            hyp.status = "supported"
        elif hyp.confidence <= 0.3:
            hyp.status = "challenged"
        else:
            hyp.status = "uncertain"

    def get_hypothesis(self, hypothesis_id: str) -> Optional[Hypothesis]:
        return self._hypotheses.get(hypothesis_id)

    def get_hypotheses_by_category(self, category: str) -> list[Hypothesis]:
        return [h for h in self._hypotheses.values() if h.category == category]

    def get_challenged_hypotheses(self) -> list[Hypothesis]:
        return [h for h in self._hypotheses.values() if h.status == "challenged"]

    # ── Query interface for agents ───────────────────────────────────────────

    def query_for_agent(self, agent_name: str, section: str) -> dict:
        """Get all relevant knowledge for an agent's section."""
        section_facts = self.get_facts_by_section(section)
        dependent_facts = []
        for f in self._facts.values():
            if f.valid and any(d in [sf.id for sf in section_facts] for d in f.dependencies):
                dependent_facts.append(f)

        challenged = self.get_challenged_hypotheses()

        return {
            "section_facts": [
                {"id": f.id, "content": f.content, "confidence": f.confidence, "from": f.provenance}
                for f in section_facts
            ],
            "upstream_facts": [
                {"id": f.id, "content": f.content, "confidence": f.confidence, "section": f.section}
                for f in dependent_facts[:10]
            ],
            "challenged_hypotheses": [
                {"id": h.id, "claim": h.claim, "confidence": h.confidence, "status": h.status}
                for h in challenged[:5]
            ],
        }

    # ── Stats ────────────────────────────────────────────────────────────────

    @property
    def stats(self) -> dict:
        valid_facts = [f for f in self._facts.values() if f.valid]
        return {
            "total_facts": len(self._facts),
            "valid_facts": len(valid_facts),
            "invalidated_facts": len(self._facts) - len(valid_facts),
            "total_hypotheses": len(self._hypotheses),
            "challenged_hypotheses": len(self.get_challenged_hypotheses()),
            "sections_covered": list(set(f.section for f in valid_facts)),
        }


# Singleton instance
_shared_knowledge_graph: Optional[KnowledgeGraph] = None


def get_knowledge_graph() -> KnowledgeGraph:
    global _shared_knowledge_graph
    if _shared_knowledge_graph is None:
        _shared_knowledge_graph = KnowledgeGraph()
    return _shared_knowledge_graph
