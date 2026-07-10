"""
Lightweight BDI (Belief-Desire-Intention) layer for Phase 2 child agents.

Gives agents persistent beliefs that survive across tasks within a session,
can be challenged by other agents, and inject into LLM prompts for continuity.

P1-1: Vector-based contradiction detection using semantic similarity.
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Source authority hierarchy — higher index = higher authority
SOURCE_AUTHORITY: dict[str, int] = {
    "own_analysis": 0,
    "section_3": 1,
    "market_data": 2,
    "ceo_input": 3,
}


def _compute_semantic_similarity(text1: str, text2: str) -> float:
    """P1-1: Compute cosine similarity between two texts using Cohere Embed v3.

    Returns:
        float between 0.0 and 1.0, where:
        - 1.0 = identical meaning
        - 0.8-1.0 = very similar (likely agreement)
        - 0.3-0.7 = somewhat related
        - 0.0-0.3 = unrelated or contradictory

    Returns -1.0 if embedding service unavailable (triggers fallback).
    """
    try:
        from services.embedding_service import embed_batch
        import numpy as np

        embeddings = embed_batch([text1, text2], input_type="classification")
        vec1 = np.array(embeddings[0])
        vec2 = np.array(embeddings[1])

        similarity = float(np.dot(vec1, vec2) / (np.linalg.norm(vec1) * np.linalg.norm(vec2)))
        return similarity
    except Exception as e:
        logger.error("[Beliefs] Failed to compute semantic similarity: %s", e)
        return -1.0


@dataclass
class Belief:
    """A single belief held by an agent."""

    claim: str
    confidence: float  # 0.0 to 1.0
    source: str  # "own_analysis", "section_3", "ceo_input", "market_data"
    established_at: str  # ISO timestamp
    challenged_by: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.confidence = max(0.0, min(1.0, self.confidence))
        if self.source not in SOURCE_AUTHORITY:
            logger.warning(
                "Unknown belief source '%s', defaulting to 'own_analysis'",
                self.source,
            )
            self.source = "own_analysis"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Belief":
        return cls(
            claim=data["claim"],
            confidence=data["confidence"],
            source=data["source"],
            established_at=data["established_at"],
            challenged_by=data.get("challenged_by", []),
        )


class AgentBeliefStore:
    """
    Per-agent belief store persisted to Redis.

    Keys are stored under the pattern:
        beliefs:{agent_name}
    as a single JSON hash to minimise Redis round-trips.
    """

    def __init__(self, agent_name: str, redis_client: Any) -> None:
        self._agent_name: str = agent_name
        self._redis = redis_client
        self._beliefs: dict[str, Belief] = {}
        self._redis_key: str = f"beliefs:{agent_name}"
        self._load()
        logger.info(
            "BeliefStore initialised for '%s' with %d beliefs",
            agent_name,
            len(self._beliefs),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def assert_belief(
        self,
        key: str,
        claim: str,
        confidence: float,
        source: str,
    ) -> None:
        """Agent asserts a belief, overwriting any previous belief at this key."""
        belief = Belief(
            claim=claim,
            confidence=confidence,
            source=source,
            established_at=datetime.now(timezone.utc).isoformat(),
        )
        self._beliefs[key] = belief
        logger.info(
            "[%s] Asserted belief '%s': %.2f confidence from %s",
            self._agent_name,
            key,
            confidence,
            source,
        )
        self._persist()

    def challenge_belief(
        self,
        key: str,
        challenger: str,
        counter_evidence: str,
    ) -> bool:
        """
        Another agent challenges a belief.

        Returns True if confidence was reduced (challenge accepted).
        Confidence is halved when:
          - 2+ distinct challengers have challenged this belief, OR
          - The challenger's implicit authority (via counter_evidence source)
            outranks the belief's source.
        """
        if key not in self._beliefs:
            logger.warning(
                "[%s] Challenge on non-existent belief '%s' from %s — ignored",
                self._agent_name,
                key,
                challenger,
            )
            return False

        belief = self._beliefs[key]

        if challenger not in belief.challenged_by:
            belief.challenged_by.append(challenger)

        logger.info(
            "[%s] Belief '%s' challenged by %s (now %d challengers). Evidence: %s",
            self._agent_name,
            key,
            challenger,
            len(belief.challenged_by),
            counter_evidence[:120],
        )

        should_reduce = self._should_reduce_confidence(belief, challenger)

        if should_reduce:
            old_confidence = belief.confidence
            belief.confidence = round(belief.confidence / 2.0, 4)
            logger.info(
                "[%s] Belief '%s' confidence reduced: %.4f -> %.4f",
                self._agent_name,
                key,
                old_confidence,
                belief.confidence,
            )
            self._persist()
            return True

        self._persist()
        return False

    def get_beliefs_for_prompt(self) -> str:
        """
        Format all beliefs as a string block suitable for LLM prompt injection.

        Returns an empty string if no beliefs are held.
        """
        if not self._beliefs:
            return ""

        lines: list[str] = ["[AGENT BELIEFS]"]
        for key, belief in self._beliefs.items():
            status = ""
            if belief.challenged_by:
                status = f" [CHALLENGED by: {', '.join(belief.challenged_by)}]"
            lines.append(
                f"- {key}: \"{belief.claim}\" "
                f"(confidence={belief.confidence:.2f}, source={belief.source}){status}"
            )
        lines.append("[/AGENT BELIEFS]")
        return "\n".join(lines)

    def get_conflicts_with(self, incoming_data: dict[str, Any]) -> list[dict[str, Any]]:
        """
        Detect conflicts between current beliefs and incoming data.

        P1-1: Uses vector-based semantic similarity for text fields.
        Falls back to keyword matching if embeddings unavailable.

        For numeric fields: >30% divergence = conflict (unchanged).
        For text fields: semantic similarity <0.5 = likely contradiction.
        """
        conflicts: list[dict[str, Any]] = []

        for key, belief in self._beliefs.items():
            if key not in incoming_data:
                continue

            incoming_value = incoming_data[key]
            conflict = self._detect_single_conflict(key, belief, incoming_value)
            if conflict:
                conflicts.append(conflict)

        if conflicts:
            logger.info(
                "[%s] Detected %d conflicts with incoming data",
                self._agent_name,
                len(conflicts),
            )

        return conflicts

    def get_semantic_conflicts(self) -> list[dict[str, Any]]:
        """P1-1: Detect contradictions BETWEEN beliefs using semantic similarity.

        This is NEW — detects when agent holds contradictory beliefs internally,
        not just conflicts with incoming data.

        Returns:
            List of conflict dicts with:
            - belief_a: (key, claim)
            - belief_b: (key, claim)
            - similarity: float (0-1)
            - conflict_type: "semantic_contradiction"
        """
        conflicts: list[dict[str, Any]] = []
        belief_items = list(self._beliefs.items())

        # Compare each pair of beliefs
        for i, (key_a, belief_a) in enumerate(belief_items):
            for key_b, belief_b in belief_items[i + 1:]:
                # Skip if beliefs are about unrelated topics (different keys)
                if not self._are_related_topics(key_a, key_b):
                    continue

                # Compute semantic similarity
                similarity = _compute_semantic_similarity(
                    belief_a.claim,
                    belief_b.claim
                )

                # Fallback if embeddings unavailable
                if similarity < 0:
                    continue

                # Threshold: similarity <0.5 suggests contradiction
                # (not similar enough to agree, but related enough to compare)
                if similarity < 0.5:
                    conflicts.append({
                        "belief_a": {"key": key_a, "claim": belief_a.claim},
                        "belief_b": {"key": key_b, "claim": belief_b.claim},
                        "similarity": round(similarity, 3),
                        "conflict_type": "semantic_contradiction",
                        "severity": self._assess_conflict_severity(
                            belief_a, belief_b, similarity
                        ),
                    })

        if conflicts:
            logger.warning(
                "[%s] Detected %d semantic contradictions between beliefs",
                self._agent_name,
                len(conflicts),
            )

        return conflicts

    def update_from_output(self, output: dict[str, Any]) -> None:
        """
        Extract beliefs from an agent's structured output and store them.

        Looks for keys that contain substantive claims (strings with 10+ chars
        or numeric values) and stores them as beliefs with own_analysis source.
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        count = 0

        for key, value in output.items():
            if self._is_belief_worthy(value):
                claim = self._value_to_claim(key, value)
                belief = Belief(
                    claim=claim,
                    confidence=0.75,  # default confidence for own output
                    source="own_analysis",
                    established_at=timestamp,
                )
                self._beliefs[key] = belief
                count += 1

        if count > 0:
            logger.info(
                "[%s] Extracted %d beliefs from output",
                self._agent_name,
                count,
            )
            self._persist()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _persist(self) -> None:
        """Serialize all beliefs to Redis."""
        try:
            payload = {
                key: belief.to_dict() for key, belief in self._beliefs.items()
            }
            self._redis.set(self._redis_key, json.dumps(payload))
            logger.debug("[%s] Persisted %d beliefs to Redis", self._agent_name, len(payload))
        except Exception as exc:
            logger.error(
                "[%s] Failed to persist beliefs to Redis: %s",
                self._agent_name,
                exc,
            )
            raise

    def _load(self) -> None:
        """Load beliefs from Redis. Silently starts empty if key missing."""
        try:
            raw = self._redis.get(self._redis_key)
            if raw is None:
                self._beliefs = {}
                return

            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")

            data = json.loads(raw)
            self._beliefs = {
                key: Belief.from_dict(belief_data)
                for key, belief_data in data.items()
            }
            logger.debug(
                "[%s] Loaded %d beliefs from Redis",
                self._agent_name,
                len(self._beliefs),
            )
        except Exception as exc:
            logger.error(
                "[%s] Failed to load beliefs from Redis: %s — starting empty",
                self._agent_name,
                exc,
            )
            self._beliefs = {}

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _should_reduce_confidence(self, belief: Belief, challenger: str) -> bool:
        """
        Determine whether a belief's confidence should be halved.

        Rules:
        1. If challenged by 2+ distinct agents → halve
        2. If challenger is a higher-authority source → halve
        """
        # Rule 1: multiple challengers
        if len(belief.challenged_by) >= 2:
            return True

        # Rule 2: higher-authority challenger
        # Challenger name may encode authority (e.g. "ceo_input", "market_data")
        challenger_authority = SOURCE_AUTHORITY.get(challenger, -1)
        belief_authority = SOURCE_AUTHORITY.get(belief.source, 0)

        if challenger_authority > belief_authority:
            return True

        return False

    def _detect_single_conflict(
        self,
        key: str,
        belief: Belief,
        incoming_value: Any,
    ) -> dict[str, Any] | None:
        """Check if a single incoming value conflicts with an existing belief.

        P1-1: Uses semantic similarity for text comparisons.
        """
        claim = belief.claim

        # Numeric comparison: >30% divergence = conflict (unchanged)
        if isinstance(incoming_value, (int, float)):
            try:
                # Extract numeric from claim if possible
                claim_numeric = self._extract_numeric_from_claim(claim)
                if claim_numeric is not None and claim_numeric != 0:
                    divergence = abs(incoming_value - claim_numeric) / abs(claim_numeric)
                    if divergence > 0.30:
                        return {
                            "key": key,
                            "existing_belief": claim,
                            "existing_confidence": belief.confidence,
                            "incoming_value": incoming_value,
                            "divergence_pct": round(divergence * 100, 1),
                            "conflict_type": "numeric_divergence",
                        }
            except (ValueError, TypeError):
                pass
            return None

        # P1-1: String comparison using semantic similarity
        if isinstance(incoming_value, str) and len(incoming_value) >= 10:
            similarity = _compute_semantic_similarity(claim, incoming_value)

            # Fallback to exact match if embeddings unavailable
            if similarity < 0:
                if incoming_value.lower().strip() != claim.lower().strip():
                    return {
                        "key": key,
                        "existing_belief": claim,
                        "existing_confidence": belief.confidence,
                        "incoming_value": incoming_value,
                        "conflict_type": "content_mismatch",
                    }
                return None

            # Semantic contradiction: similarity <0.5 = likely conflict
            if similarity < 0.5:
                return {
                    "key": key,
                    "existing_belief": claim,
                    "existing_confidence": belief.confidence,
                    "incoming_value": incoming_value,
                    "similarity": round(similarity, 3),
                    "conflict_type": "semantic_contradiction",
                }

        return None

    @staticmethod
    def _extract_numeric_from_claim(claim: str) -> float | None:
        """Attempt to extract a numeric value from a belief claim string."""
        import re

        # Match patterns like "$1.2M", "1200000", "45%", "3.5"
        patterns = [
            r"\$?([\d,]+\.?\d*)\s*[MmBbKk]?",
            r"([\d]+\.?\d*)%",
            r"([\d,]+\.?\d*)",
        ]
        for pattern in patterns:
            match = re.search(pattern, claim)
            if match:
                raw = match.group(1).replace(",", "")
                try:
                    return float(raw)
                except ValueError:
                    continue
        return None

    @staticmethod
    def _is_belief_worthy(value: Any) -> bool:
        """Determine if a value is substantive enough to store as a belief."""
        if isinstance(value, str) and len(value) >= 10:
            return True
        if isinstance(value, (int, float)) and value != 0:
            return True
        return False

    @staticmethod
    def _value_to_claim(key: str, value: Any) -> str:
        """Convert a key-value pair into a natural language claim string."""
        if isinstance(value, str):
            return value
        if isinstance(value, (int, float)):
            return f"{key} = {value}"
        return str(value)

    @staticmethod
    def _are_related_topics(key_a: str, key_b: str) -> bool:
        """P1-1: Heuristic to determine if two belief keys are related topics.

        Related topics are worth comparing for contradictions.
        Unrelated topics should not be compared (waste of compute).
        """
        # Normalize keys
        a_lower = key_a.lower().replace("_", " ")
        b_lower = key_b.lower().replace("_", " ")

        # Exact match
        if a_lower == b_lower:
            return True

        # Share any significant words (>3 chars)
        words_a = {w for w in a_lower.split() if len(w) > 3}
        words_b = {w for w in b_lower.split() if len(w) > 3}

        shared = words_a & words_b
        if shared:
            return True

        # Common business plan topics that are related
        related_clusters = [
            {"market", "tam", "sam", "som", "size", "segment"},
            {"revenue", "pricing", "ltv", "cac", "arpu", "mrr"},
            {"customer", "icp", "persona", "target", "segment"},
            {"competition", "competitor", "moat", "differentiation"},
            {"cost", "opex", "capex", "burn", "runway", "margin"},
            {"team", "headcount", "hiring", "roles", "personnel"},
        ]

        for cluster in related_clusters:
            if any(w in cluster for w in words_a) and any(w in cluster for w in words_b):
                return True

        return False

    @staticmethod
    def _assess_conflict_severity(
        belief_a: Belief,
        belief_b: Belief,
        similarity: float
    ) -> str:
        """P1-1: Assess severity of semantic contradiction.

        Returns: "critical" | "major" | "minor"
        """
        # Both beliefs have high confidence + very low similarity = critical
        if belief_a.confidence >= 0.8 and belief_b.confidence >= 0.8 and similarity < 0.3:
            return "critical"

        # One high-confidence belief contradicts medium-confidence = major
        if (belief_a.confidence >= 0.7 or belief_b.confidence >= 0.7) and similarity < 0.4:
            return "major"

        # Everything else = minor
        return "minor"
