"""
VALIDATE Workspace Handler — manages assumption lifecycle.

Handles: confirming assumptions, killing assumptions, reporting conversations,
updating decisions, showing cascade previews.
"""

import json
import logging
from typing import Optional

from services.rag_service import RagStoreError, StoreOutcome
from tools.trace_emitter import emit_trace

logger = logging.getLogger(__name__)

_KILL_CONFIRM_PREFIX = "validate_kill_pending:"
_CONFIRM_CONFIRM_PREFIX = "validate_confirm_pending:"


def _get_redis():
    from memory.redis_client import RedisClient
    return RedisClient()


def _trace(session_id: Optional[str], step: str, detail: str, data: Optional[dict] = None) -> None:
    """Emit a trace event only if we actually have a session to broadcast to."""
    if session_id:
        emit_trace(session_id, "Validate", step, detail, data or {})


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

        _trace(session_id, "matching_assumption", f"Looking up: \"{assumption_text[:60]}\"...")
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

        _trace(session_id, "storing_validation", "Recording evidence and upgrading status to CONFIRMED...")
        result = store(
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

        # Only report a confirmation the store actually holds. A duplicate is
        # fine — the evidence is already on record.
        if not result and result.outcome is not StoreOutcome.SKIPPED_DUPLICATE:
            logger.error(
                "[ValidateHandler] Evidence not persisted (%s) — assumption "
                "'%s' left unchanged",
                result.outcome.value,
                assumption_text[:80],
            )
            return {
                "success": False,
                "error": (
                    "Evidence was not persisted — the assumption status is "
                    "unchanged. Nothing was confirmed."
                ),
            }

        _trace(session_id, "checking_cascade", "Checking downstream impact on dependent sections...")
        cascade = get_cascade_preview(assumption_text, session_id=session_id)

        _trace(
            session_id, "confirm_complete",
            f"Confirmed — {cascade.get('affected_count', 0)} downstream node(s) strengthened",
        )

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
    except RagStoreError as e:
        logger.error("[ValidateHandler] Evidence write failed: %s", e)
        return {
            "success": False,
            "error": (
                "Evidence could not be written to the knowledge base — the "
                "assumption status is unchanged. Nothing was confirmed."
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

        _trace(session_id, "storing_kill", f"Marking as killed: \"{assumption_text[:60]}\"...")
        result = store(
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

        # "Never re-suggested" is only true if the negative-knowledge row landed.
        if not result and result.outcome is not StoreOutcome.SKIPPED_DUPLICATE:
            logger.error(
                "[ValidateHandler] Kill not persisted (%s) — assumption '%s' "
                "is still live",
                result.outcome.value,
                assumption_text[:80],
            )
            return {
                "success": False,
                "error": (
                    "The kill was not persisted — this assumption is still "
                    "live and may be re-suggested. Nothing was changed."
                ),
            }

        _trace(session_id, "checking_cascade", "Checking downstream impact before finalizing...")
        cascade = get_cascade_preview(assumption_text, session_id=session_id)
        affected = cascade.get("affected_count", 0)
        _trace(session_id, "kill_complete", f"Killed — {affected} downstream node(s) affected")

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
    except RagStoreError as e:
        logger.error("[ValidateHandler] Kill write failed: %s", e)
        return {
            "success": False,
            "error": (
                "The kill could not be written to the knowledge base — this "
                "assumption is still live and may be re-suggested."
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

        _trace(session_id, "logging_conversation", f"Logging conversation with {who} as CONFIRMED evidence...")
        result = store(
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

        if not result and result.outcome is not StoreOutcome.SKIPPED_DUPLICATE:
            logger.error(
                "[ValidateHandler] Conversation not persisted (%s): %s",
                result.outcome.value,
                who,
            )
            return {
                "success": False,
                "error": "The conversation was not persisted as evidence.",
            }

        _trace(session_id, "conversation_logged", f"Logged conversation with {who}")

        return {
            "success": True,
            "chunk_id": result.id or result.duplicate_of,
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

        _trace(session_id, "updating_decision", f"Superseding decision: \"{original_decision[:50]}\"...")
        new_id = store_correction(
            original_fact=f"Decision: {original_decision}",
            corrected_fact=f"Updated decision: {new_decision} (Reason: {reason})",
            session_id=session_id,
        )
        _trace(session_id, "decision_updated", "Decision updated")

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


def get_cascade_preview(assumption_text: str, session_id: Optional[str] = None) -> dict:
    """Show what changes if an assumption is confirmed or killed.

    Args:
        assumption_text: The assumption to check dependencies for.
        session_id: Current session ID, for live trace narration.

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


def get_assumption_queue(session_id: Optional[str] = None) -> dict:
    """Panel view: assumptions ranked by age + downstream impact.

    Args:
        session_id: Current session ID, for live trace narration.

    Returns:
        Dict with queue list ordered by priority.
    """
    try:
        from services.coverage_calculator import get_oldest_assumptions

        _trace(session_id, "loading_assumptions", "Loading assumptions ranked by age...")
        assumptions = get_oldest_assumptions(top_k=20)

        queue = []
        for i, a in enumerate(assumptions):
            cascade = get_cascade_preview(a["content_preview"])
            priority_score = a["age_days"] * (cascade.get("affected_count", 1) + 1)

            if assumptions and (i + 1) % 5 == 0:
                _trace(
                    session_id, "scoring_progress",
                    f"Scored {i + 1}/{len(assumptions)} assumption(s) by impact...",
                )

            queue.append({
                "id": a["id"],
                "content": a["content_preview"],
                "age_days": a["age_days"],
                "affected_count": cascade.get("affected_count", 0),
                "impact_level": cascade.get("impact_level", "unknown"),
                "priority_score": priority_score,
            })

        queue.sort(key=lambda x: x["priority_score"], reverse=True)

        _trace(session_id, "queue_ready", f"Validation queue ready — {len(queue)} assumption(s), ranked")

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

    # get_assumption_queue() (the "a" menu command — Validate's primary
    # feature) returns {"count", "queue": [...]} with no "message" key.
    # Without this branch, the code below always fell through to an empty
    # string, and server.py's fallback silently replaced it with "Received
    # — nothing to show for that input." — hiding a fully-computed,
    # correctly-ranked 20-assumption queue behind a useless message.
    if "queue" in result:
        queue = result.get("queue", [])
        count = result.get("count", len(queue))
        if count == 0:
            return "No assumptions in the queue — nothing waiting on validation right now."
        lines = [f"Validation queue — {count} assumption(s), ranked by age × downstream impact:"]
        lines.append("")
        for i, a in enumerate(queue[:15], 1):
            lines.append(
                f"  {i}. [{a.get('impact_level', 'unknown').upper()}] "
                f"{a.get('content', '')[:70]}"
            )
            lines.append(
                f"     Age: {a.get('age_days', '?')}d, "
                f"affects {a.get('affected_count', 0)} item(s)"
            )
        if count > 15:
            lines.append(f"\n  ...and {count - 15} more.")
        lines.append("")
        lines.append("Type 'validate <text>|<evidence>' to confirm one, or 'kill <text>|<reason>' to kill one.")
        return "\n".join(lines)

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


def request_kill(
    assumption_text: str,
    reason: str,
    session_id: Optional[str] = None,
) -> str:
    """Stage a kill and ask for confirmation before executing."""
    redis = _get_redis()
    key = f"{_KILL_CONFIRM_PREFIX}{session_id}"
    redis.client.set(
        key,
        json.dumps({
            "assumption_text": assumption_text,
            "reason": reason,
            "session_id": session_id,
        }),
        ex=600,
    )
    cascade = get_cascade_preview(assumption_text, session_id=session_id)
    affected = cascade.get("affected_count", 0)

    msg = (
        f"You're about to kill assumption: \"{assumption_text}\"\n"
        f"Reason: {reason}\n"
        f"Downstream impact: {affected} node(s) affected.\n\n"
        f"This is irreversible. Type 'confirm kill' to proceed or anything else to cancel."
    )
    _trace(session_id, "awaiting_kill_confirm", f"Awaiting kill confirmation for: {assumption_text[:60]}")
    return msg


def request_confirm(
    assumption_text: str,
    evidence: str,
    source: Optional[str] = None,
    session_id: Optional[str] = None,
) -> str:
    """Stage a confirm and ask for confirmation before executing."""
    redis = _get_redis()
    key = f"{_CONFIRM_CONFIRM_PREFIX}{session_id}"
    redis.client.set(
        key,
        json.dumps({
            "assumption_text": assumption_text,
            "evidence": evidence,
            "source": source,
            "session_id": session_id,
        }),
        ex=600,
    )

    msg = (
        f"You're about to validate assumption: \"{assumption_text}\"\n"
        f"Evidence: {evidence}\n\n"
        f"Type 'confirm' to mark this as validated or anything else to cancel."
    )
    _trace(session_id, "awaiting_confirm", f"Awaiting validation confirmation for: {assumption_text[:60]}")
    return msg


def handle_pending_response(text: str, session_id: Optional[str] = None) -> Optional[str]:
    """Check if there's a pending kill/confirm gate and handle the response.

    Returns:
        Response string if a gate was active (consumed), or None if no gate.
    """
    redis = _get_redis()
    text_lower = text.strip().lower()

    kill_key = f"{_KILL_CONFIRM_PREFIX}{session_id}"
    kill_pending = redis.client.get(kill_key)
    if kill_pending:
        redis.client.delete(kill_key)
        if text_lower == "confirm kill":
            data = json.loads(
                kill_pending.decode("utf-8")
                if isinstance(kill_pending, bytes)
                else kill_pending
            )
            result = kill_assumption(
                assumption_text=data["assumption_text"],
                reason=data["reason"],
                session_id=data.get("session_id"),
            )
            return format_validate_response(result)
        else:
            _trace(session_id, "kill_cancelled", "Kill cancelled by user")
            return "Kill cancelled. Assumption remains unchanged."

    confirm_key = f"{_CONFIRM_CONFIRM_PREFIX}{session_id}"
    confirm_pending = redis.client.get(confirm_key)
    if confirm_pending:
        redis.client.delete(confirm_key)
        if text_lower == "confirm":
            data = json.loads(
                confirm_pending.decode("utf-8")
                if isinstance(confirm_pending, bytes)
                else confirm_pending
            )
            result = confirm_assumption(
                assumption_text=data["assumption_text"],
                evidence=data["evidence"],
                source=data.get("source"),
                session_id=data.get("session_id"),
            )
            return format_validate_response(result)
        else:
            _trace(session_id, "confirm_cancelled", "Validation cancelled by user")
            return "Validation cancelled. Assumption remains unconfirmed."

    return None
