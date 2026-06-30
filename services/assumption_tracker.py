"""
Assumption Tracker — tracks the lifecycle of strategic assumptions.

Each assumption from Alex's register (Section 15) can move through states:
  ASSUMPTION → PARTIALLY_VALIDATED → VALIDATED → INVALIDATED

Evidence events are stored as a chain, so agents can query:
"What's the current status of the business-school-first assumption?"
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def record_evidence(
    assumption_id: str,
    evidence: str,
    effect: str,
    source: str = "conversation",
    session_id: Optional[str] = None,
    confidence_delta: float = 0.1,
    metadata: Optional[dict] = None,
) -> Optional[str]:
    """Record a piece of evidence that affects an assumption's status.

    Args:
        assumption_id: Short identifier for the assumption (e.g., "business_schools_first").
        evidence: Description of what was learned.
        effect: One of "supports", "challenges", "invalidates", "partially_validates".
        source: Where the evidence came from (interview, pilot, advisor, etc.).
        session_id: Current session ID.
        confidence_delta: How much this evidence moves the confidence needle.
        metadata: Additional context.

    Returns:
        Chunk ID of the stored evidence event.
    """
    from services.rag_service import store

    valid_effects = {"supports", "challenges", "invalidates", "partially_validates"}
    if effect not in valid_effects:
        logger.warning(
            "[Assumptions] Invalid effect '%s', must be one of %s",
            effect,
            valid_effects,
        )
        return None

    status_map = {
        "supports": "ASSUMPTION",
        "partially_validates": "CONFIRMED",
        "challenges": "CONTRADICTION",
        "invalidates": "MISSING",
    }

    content = (
        f"ASSUMPTION UPDATE [{assumption_id}]: "
        f"Evidence {effect} this assumption. "
        f"Evidence: {evidence}. Source: {source}."
    )

    return store(
        content=content,
        source_type="assumption_lifecycle",
        epistemic_status=status_map.get(effect, "ASSUMPTION"),
        session_id=session_id,
        topic_tags=["assumption", assumption_id, effect],
        confidence=min(1.0, max(0.0, confidence_delta)),
        metadata={
            **(metadata or {}),
            "assumption_id": assumption_id,
            "effect": effect,
            "source": source,
        },
    )


def get_assumption_status(assumption_id: str) -> dict:
    """Get the current status of an assumption based on all evidence.

    Args:
        assumption_id: The assumption identifier.

    Returns:
        Dict with: current_status, evidence_count, supports, challenges, confidence.
    """
    from services.rag_service import retrieve

    chunks = retrieve(
        query=f"assumption {assumption_id}",
        source_types=["assumption_lifecycle"],
        top_k=20,
        threshold=0.4,
    )

    relevant = [
        c for c in chunks
        if assumption_id in (c.metadata or {}).get("assumption_id", "")
        or assumption_id in c.content
    ]

    if not relevant:
        return {
            "assumption_id": assumption_id,
            "current_status": "ASSUMPTION",
            "evidence_count": 0,
            "supports": 0,
            "challenges": 0,
            "confidence": 0.3,
        }

    supports = 0
    challenges = 0
    for chunk in relevant:
        effect = (chunk.metadata or {}).get("effect", "")
        if effect in ("supports", "partially_validates"):
            supports += 1
        elif effect in ("challenges", "invalidates"):
            challenges += 1

    total = supports + challenges
    if total == 0:
        confidence = 0.3
        status = "ASSUMPTION"
    elif challenges == 0:
        confidence = min(0.9, 0.4 + supports * 0.15)
        status = "CONFIRMED" if confidence > 0.7 else "ASSUMPTION"
    elif supports == 0:
        confidence = max(0.1, 0.4 - challenges * 0.15)
        status = "CONTRADICTION" if challenges >= 2 else "ASSUMPTION"
    else:
        confidence = 0.3 + (supports - challenges) * 0.1
        confidence = min(0.9, max(0.1, confidence))
        status = "ASSUMPTION" if abs(supports - challenges) < 2 else (
            "CONFIRMED" if supports > challenges else "CONTRADICTION"
        )

    return {
        "assumption_id": assumption_id,
        "current_status": status,
        "evidence_count": len(relevant),
        "supports": supports,
        "challenges": challenges,
        "confidence": round(confidence, 2),
    }


def get_all_assumption_statuses() -> list[dict]:
    """Get status of all tracked assumptions.

    Returns:
        List of status dicts for each assumption that has evidence.
    """
    from services.rag_service import retrieve

    chunks = retrieve(
        query="assumption evidence update",
        source_types=["assumption_lifecycle"],
        top_k=50,
        threshold=0.3,
    )

    assumption_ids = set()
    for chunk in chunks:
        aid = (chunk.metadata or {}).get("assumption_id")
        if aid:
            assumption_ids.add(aid)

    return [get_assumption_status(aid) for aid in sorted(assumption_ids)]
