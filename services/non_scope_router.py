"""
Non-Scope Router — handles facts that don't fit any SSoT node.

When the precision mapper can't find a suitable node (confidence < threshold),
the fact is routed here for human review rather than being forced into the framework.

Storage: Supabase `knowledge_base`, NOT local disk. These are unreviewed CEO
facts — losing them on a redeploy loses data nobody knows to re-enter. Items are
stored as source_type='ceo_doc' with epistemic_status='MISSING' (they are not yet
placed anywhere in the plan) and carry their queue state in
metadata.non_scope = {status, reason, confidence}.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

NON_SCOPE_TAGS = ["non-scope", "pending-review"]
_STATUS_PATH = "metadata->non_scope->>status"


def _to_item(row: dict) -> dict:
    """Map a knowledge_base row to the non-scope queue item shape."""
    metadata = row.get("metadata") or {}
    non_scope = metadata.get("non_scope") or {}
    return {
        "id": row["id"],
        "fact": row.get("content", ""),
        "reason": non_scope.get("reason", ""),
        "confidence": non_scope.get("confidence", row.get("confidence") or 0.0),
        "session_id": row.get("session_id"),
        "metadata": {k: v for k, v in metadata.items() if k != "non_scope"},
        "status": non_scope.get("status", "pending"),
        "created_at": row.get("created_at"),
        "resolution": non_scope.get("resolution"),
    }


def route_to_non_scope(
    fact: str,
    reason: str,
    confidence: float = 0.0,
    session_id: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> str:
    """Store a fact in the non-scope queue for human review.

    Args:
        fact: The fact text that doesn't fit any node.
        reason: Why it was routed to non-scope.
        confidence: The mapping confidence that was too low.
        session_id: Current session ID.
        metadata: Additional context.

    Returns:
        The knowledge_base UUID of the non-scope item.

    Raises:
        RagStoreError: The item could not be persisted. Never swallowed — an
            unqueued fact is an invisibly lost fact.
    """
    from services.rag_service import store

    result = store(
        content=fact,
        source_type="ceo_doc",
        epistemic_status="MISSING",
        topic_tags=list(NON_SCOPE_TAGS),
        session_id=session_id,
        confidence=confidence,
        metadata={
            **(metadata or {}),
            "non_scope": {
                "status": "pending",
                "reason": reason,
                "confidence": confidence,
            },
        },
    )

    # A duplicate means this fact is already sitting in the queue.
    item_id = result.id or result.duplicate_of

    logger.info(
        "[NonScope] Routed fact to non-scope: id=%s, reason=%s (%s)",
        item_id,
        reason,
        result.outcome.value,
    )
    return item_id


def get_non_scope_queue() -> list[dict]:
    """Get all pending non-scope items awaiting review.

    Returns:
        List of pending items with id, fact, reason, created_at.
    """
    from services.rag_service import TABLE_NAME, _get_supabase

    rows = (
        _get_supabase()
        .table(TABLE_NAME)
        .select("*")
        .eq("source_type", "ceo_doc")
        .contains("topic_tags", ["non-scope"])
        .eq(_STATUS_PATH, "pending")
        .order("created_at")
        .execute()
        .data
        or []
    )
    return [_to_item(row) for row in rows]


def get_non_scope_count() -> int:
    """Get the count of pending non-scope items."""
    from services.rag_service import TABLE_NAME, _get_supabase

    result = (
        _get_supabase()
        .table(TABLE_NAME)
        .select("id", count="exact")
        .eq("source_type", "ceo_doc")
        .contains("topic_tags", ["non-scope"])
        .eq(_STATUS_PATH, "pending")
        .execute()
    )
    if result.count is not None:
        return result.count
    return len(result.data or [])


def resolve_non_scope(
    item_id: str,
    action: str,
    target_node: Optional[str] = None,
    reason: Optional[str] = None,
) -> dict:
    """Resolve a non-scope item (map to node or discard).

    On "map_to_node" the mapped fact is written FIRST. Only if that write
    succeeds is the item marked resolved — otherwise the item stays in the
    queue and can be retried, instead of leaving the queue with the mapping
    stored nowhere.

    Args:
        item_id: The non-scope item ID (knowledge_base UUID).
        action: "map_to_node" or "discard".
        target_node: If mapping, which node to map to.
        reason: Why this resolution was chosen.

    Returns:
        Dict with: success, item, action.
    """
    from services.rag_service import TABLE_NAME, _get_supabase

    supabase = _get_supabase()
    rows = (
        supabase.table(TABLE_NAME).select("*").eq("id", item_id).limit(1).execute().data
        or []
    )
    if not rows:
        return {
            "success": False,
            "error": f"Item {item_id} not found in pending queue.",
        }

    row = rows[0]
    metadata = row.get("metadata") or {}
    non_scope = dict(metadata.get("non_scope") or {})
    if non_scope.get("status") == "resolved":
        return {
            "success": False,
            "error": f"Item {item_id} is already resolved.",
        }

    item = _to_item(row)

    if action == "map_to_node" and target_node:
        try:
            _store_resolved_mapping(item, target_node)
        except Exception as e:  # noqa: BLE001 — reported to the caller below
            logger.error(
                "[NonScope] Mapping write failed for %s → %s: %s",
                item_id,
                target_node,
                e,
            )
            return {
                "success": False,
                "error": (
                    f"Could not store the mapping to {target_node}: {e}. "
                    f"The item is still in the review queue."
                ),
            }

    resolution = {
        "action": action,
        "target_node": target_node,
        "reason": reason,
        "resolved_at": datetime.now(timezone.utc).isoformat(),
    }
    non_scope["status"] = "resolved"
    non_scope["resolution"] = resolution
    metadata["non_scope"] = non_scope

    updated = (
        supabase.table(TABLE_NAME)
        .update({"metadata": metadata})
        .eq("id", item_id)
        .execute()
    )
    if not updated.data:
        logger.error("[NonScope] Failed to mark %s resolved: %s", item_id, updated)
        return {
            "success": False,
            "error": f"Could not mark item {item_id} resolved.",
        }

    logger.info(
        "[NonScope] Resolved item %s: action=%s, node=%s",
        item_id,
        action,
        target_node,
    )

    item["status"] = "resolved"
    item["resolution"] = resolution

    return {
        "success": True,
        "item": item,
        "action": action,
    }


def _store_resolved_mapping(item: dict, target_node: str) -> Optional[str]:
    """Store a manually resolved mapping in the RAG store.

    This is the ONLY persistence of the resolved mapping — the queue item's own
    row stays epistemic_status='MISSING' and is not retrievable as a placed
    fact. A failure here therefore must propagate, not be logged and dropped.

    Returns:
        Chunk ID of the mapped fact, or None if it was already stored.
    """
    from services.rag_service import store

    section = target_node.split(".")[1] if "." in target_node else None

    result = store(
        content=item["fact"],
        source_type="ceo_doc",
        section=section,
        epistemic_status="INFERRED",
        topic_tags=["manual-mapping", "non-scope-resolved", target_node],
        metadata={
            "mapped_to_node": target_node,
            "originally_non_scope": True,
            "non_scope_id": item["id"],
        },
    )
    return result.id or result.duplicate_of
