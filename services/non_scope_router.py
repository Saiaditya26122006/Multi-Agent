"""
Non-Scope Router — handles facts that don't fit any SSoT node.

When the precision mapper can't find a suitable node (confidence < threshold),
the fact is routed here for human review rather than being forced into the framework.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

NON_SCOPE_FILE = Path(__file__).parent.parent / "ceo_data" / "non_scope.json"


def _load_non_scope() -> dict:
    """Load the non-scope file, creating it if needed."""
    if NON_SCOPE_FILE.exists():
        with open(NON_SCOPE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "_meta": {
            "purpose": "Facts that don't fit any SSoT node — awaiting human review",
            "created": datetime.now(timezone.utc).isoformat(),
        },
        "pending": [],
        "resolved": [],
    }


def _save_non_scope(data: dict) -> None:
    """Save the non-scope file."""
    data["_meta"]["last_updated"] = datetime.now(timezone.utc).isoformat()
    with open(NON_SCOPE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


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
        The ID of the non-scope item (index-based).
    """
    data = _load_non_scope()

    item = {
        "id": f"ns_{len(data['pending']) + len(data['resolved']) + 1:04d}",
        "fact": fact,
        "reason": reason,
        "confidence": confidence,
        "session_id": session_id,
        "metadata": metadata or {},
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    data["pending"].append(item)
    _save_non_scope(data)

    logger.info(
        "[NonScope] Routed fact to non-scope: id=%s, reason=%s",
        item["id"],
        reason,
    )
    return item["id"]


def get_non_scope_queue() -> list[dict]:
    """Get all pending non-scope items awaiting review.

    Returns:
        List of pending items with id, fact, reason, created_at.
    """
    data = _load_non_scope()
    return data.get("pending", [])


def get_non_scope_count() -> int:
    """Get the count of pending non-scope items."""
    data = _load_non_scope()
    return len(data.get("pending", []))


def resolve_non_scope(
    item_id: str,
    action: str,
    target_node: Optional[str] = None,
    reason: Optional[str] = None,
) -> dict:
    """Resolve a non-scope item (map to node or discard).

    Args:
        item_id: The non-scope item ID.
        action: "map_to_node" or "discard".
        target_node: If mapping, which node to map to.
        reason: Why this resolution was chosen.

    Returns:
        Dict with: success, item, action.
    """
    data = _load_non_scope()
    pending = data.get("pending", [])

    item_idx = None
    for i, item in enumerate(pending):
        if item["id"] == item_id:
            item_idx = i
            break

    if item_idx is None:
        return {
            "success": False,
            "error": f"Item {item_id} not found in pending queue.",
        }

    item = pending.pop(item_idx)
    item["status"] = "resolved"
    item["resolution"] = {
        "action": action,
        "target_node": target_node,
        "reason": reason,
        "resolved_at": datetime.now(timezone.utc).isoformat(),
    }

    data["resolved"].append(item)
    _save_non_scope(data)

    logger.info(
        "[NonScope] Resolved item %s: action=%s, node=%s",
        item_id,
        action,
        target_node,
    )

    if action == "map_to_node" and target_node:
        _store_resolved_mapping(item, target_node)

    return {
        "success": True,
        "item": item,
        "action": action,
    }


def _store_resolved_mapping(item: dict, target_node: str) -> None:
    """Store a manually resolved mapping in the RAG store."""
    try:
        from services.rag_service import store

        section = target_node.split(".")[1] if "." in target_node else None

        store(
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
    except Exception as e:
        logger.error("[NonScope] Error storing resolved mapping: %s", e)
