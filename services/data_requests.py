"""Build v2 Phase 2 — the Build↔Feed data-request handshake.

When a section-agent can't get data it needs, it emits a structured data_request
(not a chat line) naming the BP node(s) that would satisfy it, and the section
goes `blocked_on_data`. When Alex feeds data that classifies to one of those
nodes, Feed calls try_fulfill(): the request closes and the section unblocks and
becomes runnable again — no manual reconnection.

Degrades gracefully: if the data_requests table isn't present yet (migration 005
not applied), every function no-ops with a log so Feed and Build never break.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


def _sb():
    from services.section_state import _get_sb  # service-role client (RLS)

    return _get_sb()


def _node_matches(fact_node: str, targets: list[str]) -> bool:
    """A fact satisfies a target if it IS the target, sits under it, or the
    target sits under it (section vs leaf both count)."""
    if not fact_node or not targets:
        return False
    for t in targets:
        if not t:
            continue
        if fact_node == t or fact_node.startswith(t + ".") or t.startswith(fact_node + "."):
            return True
    return False


def create(session_id: str, section_id: str, target_nodes: list[str],
           description: str, why: Optional[str] = None, agent: Optional[str] = None) -> Optional[dict]:
    """Emit a data request and mark the section blocked_on_data."""
    try:
        row = {
            "session_id": session_id,
            "section_id": section_id,
            "agent": agent,
            "target_nodes": target_nodes or [],
            "description": description,
            "why": why,
            "status": "open",
        }
        res = _sb().table("data_requests").insert(row).execute()
        # Reflect the block on the section board.
        try:
            from services import section_state
            sec = section_state.get_section(session_id, section_id)
            if sec and sec.get("status") in ("in_progress", "not_started"):
                section_state.update_section(session_id, section_id,
                                             status="blocked_on_data", blocked_on=description)
        except Exception as e:  # noqa: BLE001
            logger.debug("[DataRequests] could not block section: %s", e)
        return res.data[0] if res.data else None
    except Exception as e:  # noqa: BLE001
        logger.warning("[DataRequests] create failed (table missing?): %s", e)
        return None


def list_open(session_id: str) -> list[dict]:
    """The 'Needs your data' inbox for a session."""
    try:
        return _sb().table("data_requests").select("*").eq(
            "session_id", session_id
        ).eq("status", "open").execute().data or []
    except Exception as e:  # noqa: BLE001
        logger.debug("[DataRequests] list_open failed: %s", e)
        return []


def try_fulfill(session_id: str, fact_node: str, chunk_id: Optional[str] = None) -> list[dict]:
    """Called when Feed stores a fact. Closes any open request whose target the
    fact satisfies, unblocks that section, and returns the fulfilled requests."""
    if not session_id or not fact_node:
        return []
    fulfilled = []
    try:
        for req in list_open(session_id):
            if not _node_matches(fact_node, req.get("target_nodes") or []):
                continue
            _sb().table("data_requests").update({
                "status": "fulfilled",
                "fulfilled_by_chunk": chunk_id,
                "fulfilled_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", req["id"]).execute()
            # Unblock the section (blocked_on_data -> not_started; becomes ready
            # if deps are met).
            try:
                from services import section_state
                sec = section_state.get_section(session_id, req["section_id"])
                if sec and sec.get("status") == "blocked_on_data":
                    section_state.update_section(session_id, req["section_id"],
                                                 status="not_started", blocked_on=None)
            except Exception as e:  # noqa: BLE001
                logger.debug("[DataRequests] unblock failed: %s", e)
            fulfilled.append(req)
            logger.info("[DataRequests] fulfilled %s for section %s via node %s",
                        req["id"], req["section_id"], fact_node)
    except Exception as e:  # noqa: BLE001
        logger.debug("[DataRequests] try_fulfill failed: %s", e)
    return fulfilled


def cancel(request_id: str) -> bool:
    try:
        _sb().table("data_requests").update({"status": "cancelled"}).eq("id", request_id).execute()
        return True
    except Exception as e:  # noqa: BLE001
        logger.debug("[DataRequests] cancel failed: %s", e)
        return False
