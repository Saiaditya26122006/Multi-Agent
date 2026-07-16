"""
BP.12 Register — governance register for unresolved items.

All contradictions, evidence gaps, unresolved assumptions, and prohibited
inferences are tracked here until a controller (Alex) reviews and resolves
them. The AI never auto-resolves governance items.

This is the system's "open questions about evidence" — the items that
need human judgment before they can be used, killed, or promoted.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

TABLE_NAME = "bp12_register"


def _get_supabase():
    """Lazy-load the Supabase client."""
    from services.rag_service import _get_supabase as get_sb
    return get_sb()


def create_register_item(
    item_type: str,
    title: str,
    description: Optional[str] = None,
    affected_chunk_ids: Optional[list[str]] = None,
    affected_node_ids: Optional[list[str]] = None,
    affected_assumption_ids: Optional[list[str]] = None,
    severity: str = "medium",
    source_session_id: Optional[str] = None,
) -> Optional[str]:
    """Create a new BP.12 register item.

    Args:
        item_type: contradiction, evidence_gap, unresolved_assumption,
                   prohibited_inference, source_conflict, sufficiency_failure.
        title: Short description of the issue.
        description: Detailed explanation.
        affected_chunk_ids: Knowledge base chunks involved.
        affected_node_ids: BP nodes affected by this issue.
        affected_assumption_ids: Assumption chunks that may be invalidated.
        severity: critical/high/medium/low.
        source_session_id: Session that triggered this item.

    Returns:
        UUID of the created register item, or None on failure.
    """
    try:
        supabase = _get_supabase()
        record = {
            "item_type": item_type,
            "title": title,
            "description": description,
            "affected_chunk_ids": affected_chunk_ids or [],
            "affected_node_ids": affected_node_ids or [],
            "affected_assumption_ids": affected_assumption_ids or [],
            "severity": severity,
            "resolution_status": "open",
            "source_session_id": source_session_id,
        }
        result = supabase.table(TABLE_NAME).insert(record).execute()
        if result.data:
            item_id = result.data[0]["id"]
            logger.info(
                "[BP12] Created register item: %s (%s) — %s",
                item_type, severity, title[:60],
            )
            return item_id
        return None
    except Exception as e:
        logger.error("[BP12] Failed to create register item: %s", e)
        return None


def get_open_items(
    item_type: Optional[str] = None,
    severity: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    """Get all open (unresolved) register items.

    Args:
        item_type: Optional filter by type.
        severity: Optional filter by severity.
        limit: Max items to return.

    Returns:
        List of register item dicts.
    """
    try:
        supabase = _get_supabase()
        q = (
            supabase.table(TABLE_NAME)
            .select("*")
            .in_("resolution_status", ["open", "under_review"])
            .order("created_at", desc=True)
            .limit(limit)
        )
        if item_type:
            q = q.eq("item_type", item_type)
        if severity:
            q = q.eq("severity", severity)
        result = q.execute()
        return result.data or []
    except Exception as e:
        logger.error("[BP12] Error fetching open items: %s", e)
        return []


def resolve_item(
    item_id: str,
    decision: str,
    reasoning: str,
    resolution_status: str = "resolved",
) -> bool:
    """Resolve a register item with a controller decision.

    Args:
        item_id: UUID of the register item.
        decision: What was decided (e.g. "kill assumption", "accept risk",
                  "reclassify as confirmed", "create investigation task").
        reasoning: Why this decision was made.
        resolution_status: resolved/accepted_risk/escalated.

    Returns:
        True if successful.
    """
    try:
        supabase = _get_supabase()
        result = (
            supabase.table(TABLE_NAME)
            .update({
                "resolution_status": resolution_status,
                "controller_decision": decision,
                "controller_reasoning": reasoning,
                "controller_decided_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
            .eq("id", item_id)
            .execute()
        )
        if result.data:
            logger.info("[BP12] Resolved item %s: %s", item_id[:8], decision)
            return True
        return False
    except Exception as e:
        logger.error("[BP12] Failed to resolve item %s: %s", item_id, e)
        return False


def add_task_to_item(item_id: str, task_id: str) -> bool:
    """Link a created task to a register item."""
    try:
        supabase = _get_supabase()
        existing = (
            supabase.table(TABLE_NAME)
            .select("created_task_ids")
            .eq("id", item_id)
            .limit(1)
            .execute()
        )
        if not existing.data:
            return False
        current_tasks = existing.data[0].get("created_task_ids") or []
        if task_id not in current_tasks:
            current_tasks.append(task_id)
        result = (
            supabase.table(TABLE_NAME)
            .update({
                "created_task_ids": current_tasks,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
            .eq("id", item_id)
            .execute()
        )
        return bool(result.data)
    except Exception as e:
        logger.error("[BP12] Failed to add task to item %s: %s", item_id, e)
        return False


def get_items_for_chunk(chunk_id: str) -> list[dict]:
    """Get all register items that reference a given chunk."""
    try:
        supabase = _get_supabase()
        result = (
            supabase.table(TABLE_NAME)
            .select("*")
            .contains("affected_chunk_ids", [chunk_id])
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.error("[BP12] Error fetching items for chunk %s: %s", chunk_id, e)
        return []


def get_items_for_node(node_id: str) -> list[dict]:
    """Get all register items that affect a given node."""
    try:
        supabase = _get_supabase()
        result = (
            supabase.table(TABLE_NAME)
            .select("*")
            .contains("affected_node_ids", [node_id])
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.error("[BP12] Error fetching items for node %s: %s", node_id, e)
        return []
