"""
VALIDATE Workspace Handler — manages assumption lifecycle.

Handles: confirming assumptions, killing assumptions, reporting conversations,
updating decisions, showing cascade previews.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def confirm_assumption(
    assumption_text: str,
    evidence: str,
    source: Optional[str] = None,
    session_id: Optional[str] = None,
) -> dict:
    """Upgrade an ASSUMPTION to CONFIRMED with evidence.

    Args:
        assumption_text: The assumption being confirmed.
        evidence: The evidence that confirms it.
        source: Source of evidence (e.g., "customer interview", "data analysis").
        session_id: Current session ID.

    Returns:
        Dict with: success, cascade_effect, new_status.
    """
    try:
        from services.assumption_tracker import record_evidence
        from services.rag_service import retrieve, store

        related = retrieve(
            query=assumption_text,
            source_types=["ceo_doc", "conversation"],
            top_k=3,
            threshold=0.6,
        )

        assumption_chunk = None
        for chunk in related:
            if chunk.epistemic_status == "ASSUMPTION":
                assumption_chunk = chunk
                break

        store(
            content=f"VALIDATED: {assumption_text} — Evidence: {evidence}",
            source_type="assumption_lifecycle",
            epistemic_status="CONFIRMED",
            topic_tags=["validation", "confirmed"],
            session_id=session_id,
            metadata={
                "original_assumption": assumption_text,
                "evidence": evidence,
                "source": source,
                "action": "confirm",
            },
        )

        cascade = get_cascade_preview(assumption_text)

        return {
            "success": True,
            "assumption": assumption_text,
            "new_status": "CONFIRMED",
            "evidence": evidence,
            "source": source,
            "cascade_effect": cascade,
            "message": (
                f"Assumption confirmed. "
                f"{cascade.get('affected_count', 0)} downstream node(s) strengthened."
            ),
        }
    except Exception as e:
        logger.error("[ValidateHandler] Error confirming assumption: %s", e)
        return {"success": False, "error": str(e)}


def kill_assumption(
    assumption_text: str,
    reason: str,
    session_id: Optional[str] = None,
) -> dict:
    """Mark an assumption as killed and cascade to negative knowledge.

    Args:
        assumption_text: The assumption being killed.
        reason: Why it was killed.
        session_id: Current session ID.

    Returns:
        Dict with: success, cascade_effect, warning.
    """
    try:
        from services.rag_service import store
        from services.conversation_store import store_decision

        store(
            content=f"KILLED ASSUMPTION: {assumption_text} — Reason: {reason}",
            source_type="negative_knowledge",
            epistemic_status="SUPERSEDED",
            topic_tags=["killed", "negative-knowledge"],
            session_id=session_id,
            metadata={
                "original_assumption": assumption_text,
                "reason": reason,
                "action": "kill",
            },
        )

        cascade = get_cascade_preview(assumption_text)
        affected = cascade.get("affected_count", 0)

        warning = None
        if affected > 3:
            warning = (
                f"WARNING: This assumption has {affected} downstream dependencies. "
                f"Killing it may invalidate significant portions of the plan."
            )

        return {
            "success": True,
            "assumption": assumption_text,
            "new_status": "KILLED",
            "reason": reason,
            "cascade_effect": cascade,
            "warning": warning,
            "message": (
                f"Assumption killed. {affected} downstream node(s) affected. "
                f"This data will never be re-suggested."
            ),
        }
    except Exception as e:
        logger.error("[ValidateHandler] Error killing assumption: %s", e)
        return {"success": False, "error": str(e)}


def report_conversation(
    summary: str,
    who: str,
    outcome: str,
    session_id: Optional[str] = None,
) -> dict:
    """Log a customer/stakeholder conversation as evidence.

    Args:
        summary: Summary of the conversation.
        who: Who the conversation was with.
        outcome: Key outcome or insight.
        session_id: Current session ID.

    Returns:
        Dict with: success, stored_as, implications.
    """
    try:
        from services.rag_service import store

        content = (
            f"Conversation with {who}: {summary}. "
            f"Outcome: {outcome}"
        )

        chunk_id = store(
            content=content,
            source_type="conversation",
            epistemic_status="CONFIRMED",
            topic_tags=["stakeholder-conversation", "evidence", who.lower().replace(" ", "-")],
            session_id=session_id,
            metadata={
                "who": who,
                "summary": summary,
                "outcome": outcome,
                "type": "external_conversation",
            },
        )

        return {
            "success": True,
            "chunk_id": chunk_id,
            "who": who,
            "summary": summary,
            "outcome": outcome,
            "message": (
                f"Conversation with {who} logged as CONFIRMED evidence. "
                f"Use this to validate related assumptions."
            ),
        }
    except Exception as e:
        logger.error("[ValidateHandler] Error reporting conversation: %s", e)
        return {"success": False, "error": str(e)}


def update_decision(
    original_decision: str,
    new_decision: str,
    reason: str,
    session_id: Optional[str] = None,
) -> dict:
    """Change a prior Yes/Adjust/Kill decision.

    Args:
        original_decision: What was decided before.
        new_decision: The new decision.
        reason: Why it changed.
        session_id: Current session ID.

    Returns:
        Dict with: success, old, new, reason.
    """
    try:
        from services.conversation_store import store_correction

        new_id = store_correction(
            original_fact=f"Decision: {original_decision}",
            corrected_fact=f"Updated decision: {new_decision} (Reason: {reason})",
            session_id=session_id,
        )

        return {
            "success": True,
            "old_decision": original_decision,
            "new_decision": new_decision,
            "reason": reason,
            "chunk_id": new_id,
            "message": f"Decision updated. Old decision superseded.",
        }
    except Exception as e:
        logger.error("[ValidateHandler] Error updating decision: %s", e)
        return {"success": False, "error": str(e)}


def get_cascade_preview(assumption_text: str) -> dict:
    """Show what changes if an assumption is confirmed or killed.

    Args:
        assumption_text: The assumption to check dependencies for.

    Returns:
        Dict with affected_count, affected_sections, impact_level.
    """
    try:
        from services.rag_service import retrieve

        related = retrieve(
            query=assumption_text,
            top_k=10,
            threshold=0.4,
        )

        affected_sections = set()
        for chunk in related:
            if chunk.section:
                affected_sections.add(chunk.section)

        affected_count = len(related)
        impact = "low"
        if affected_count > 5:
            impact = "high"
        elif affected_count > 2:
            impact = "medium"

        return {
            "affected_count": affected_count,
            "affected_sections": list(affected_sections),
            "impact_level": impact,
        }
    except Exception as e:
        logger.error("[ValidateHandler] Error computing cascade: %s", e)
        return {"affected_count": 0, "affected_sections": [], "impact_level": "unknown"}


def get_assumption_queue() -> dict:
    """Panel view: assumptions ranked by age + downstream impact.

    Returns:
        Dict with queue list ordered by priority.
    """
    try:
        from services.coverage_calculator import get_oldest_assumptions

        assumptions = get_oldest_assumptions(top_k=20)

        queue = []
        for a in assumptions:
            cascade = get_cascade_preview(a["content_preview"])
            priority_score = a["age_days"] * (cascade.get("affected_count", 1) + 1)

            queue.append({
                "id": a["id"],
                "content": a["content_preview"],
                "age_days": a["age_days"],
                "affected_count": cascade.get("affected_count", 0),
                "impact_level": cascade.get("impact_level", "unknown"),
                "priority_score": priority_score,
            })

        queue.sort(key=lambda x: x["priority_score"], reverse=True)

        return {
            "count": len(queue),
            "queue": queue,
        }
    except Exception as e:
        logger.error("[ValidateHandler] Error getting assumption queue: %s", e)
        return {"count": 0, "queue": []}


def format_validate_response(result: dict) -> str:
    """Format validation results as a chat message.

    Args:
        result: Output from any validate function.

    Returns:
        Formatted string for chat.
    """
    if not result.get("success", True):
        return f"Error: {result.get('error', 'Unknown error')}"

    message = result.get("message", "")

    cascade = result.get("cascade_effect")
    if cascade and cascade.get("affected_count", 0) > 0:
        sections = ", ".join(cascade.get("affected_sections", []))
        message += f"\n\nCascade effect: {cascade['affected_count']} related items in sections: {sections or 'N/A'}"
        message += f"\nImpact level: {cascade.get('impact_level', 'unknown').upper()}"

    warning = result.get("warning")
    if warning:
        message += f"\n\n⚠ {warning}"

    return message
