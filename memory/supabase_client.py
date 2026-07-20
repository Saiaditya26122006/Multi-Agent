"""
Supabase Client for Multi-Agent AI System
Provides database operations for CEO context, sessions, messages, and event logging.
"""

import os
import json
import logging
from typing import Optional, Dict, Any
from pathlib import Path
from dotenv import load_dotenv
from supabase import create_client, Client

logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

# Initialize Supabase client
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")

if not SUPABASE_URL or not SUPABASE_ANON_KEY:
    raise ValueError("Missing SUPABASE_URL or SUPABASE_ANON_KEY in environment variables")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)


def get_ceo_context() -> Optional[Dict[str, Any]]:
    """
    Fetches the single row from ceo_context table.

    Returns:
        Dict containing CEO context data or None if empty
    """
    try:
        response = supabase.table("ceo_context").select("*").limit(1).execute()

        if response.data and len(response.data) > 0:
            return response.data[0]
        return None

    except Exception as e:
        print(f"Error fetching CEO context: {e}")
        return None


TOPIC_CHANGE_TRIGGERS = [
    "new idea:",
    "new idea",
    "switching topics",
    "start fresh",
    "something new",
]


def _has_explicit_topic_change_trigger(message_text: str) -> bool:
    """Returns True if message contains an explicit topic-switch phrase."""
    text_lower = message_text.lower().strip()
    return any(trigger in text_lower for trigger in TOPIC_CHANGE_TRIGGERS)


def _is_topic_change(new_message: str, idea_text: str) -> bool:
    """
    Fast heuristic: returns True if new_message is semantically different
    from the session's original idea_text.

    Rule: fewer than 2 significant words (>4 chars) overlap → new topic.
    """
    if not idea_text:
        return False

    def significant_words(text: str) -> set:
        return {w.lower() for w in text.split() if len(w) > 4}

    new_words = significant_words(new_message)
    idea_words = significant_words(idea_text)

    if not new_words or not idea_words:
        return False

    overlap = new_words & idea_words
    return len(overlap) < 2


def get_active_session(
    chat_id: str,
    message_text: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Finds the most recent session where state is NOT 'COMPLETED'.

    If message_text is provided and the session has an idea_text, performs a
    topic-change check. On topic change, closes the old session and returns None
    so a new session gets created.

    Args:
        chat_id: Chat ID to filter by
        message_text: Incoming message text for topic-change detection (optional)

    Returns:
        Dict containing session data or None if no active session found
    """
    try:
        response = (
            supabase.table("sessions")
            .select("*")
            .eq("chat_id", chat_id)
            .neq("state", "COMPLETED")
            .order("started_at", desc=True)
            .limit(1)
            .execute()
        )

        if not response.data or len(response.data) == 0:
            return None

        session = response.data[0]

        if message_text:
            is_topic_switch = _has_explicit_topic_change_trigger(message_text)

            if not is_topic_switch and session.get("idea_text"):
                ACTIVE_CONVERSATION_STATES = [
                    "NEEDS_CLARIFICATION",
                    "AWAITING_RESEARCH",
                    "RESEARCH_RUNNING",
                    "AWAITING_FEEDBACK",
                    "AWAITING_APPROVAL",
                ]
                session_state = session.get("state", "")

                # Word-overlap heuristic only runs outside active conversation.
                # During active states, the message is an answer — not a new idea.
                if session_state not in ACTIVE_CONVERSATION_STATES:
                    is_topic_switch = _is_topic_change(
                        message_text, session["idea_text"]
                    )

            if is_topic_switch:
                session_id = session.get("id")
                old_idea_text = session.get("idea_text", "unnamed idea")
                if old_idea_text.startswith("Previous idea ("):
                    old_idea_text = "unnamed idea"
                logger.info(
                    "[L0] Topic change detected — closing session %s, "
                    "starting fresh",
                    session_id,
                )

                update_payload = {"state": "COMPLETED"}

                try:
                    from memory.redis_client import get_session_state, redis_client

                    redis_state = get_session_state(session_id)
                    if redis_state:
                        update_payload["archived_state"] = json.dumps(
                            redis_state
                        )
                except Exception as e:
                    logger.warning(
                        "[L0] Redis unavailable, skipping archived_state "
                        "write: %s",
                        e,
                    )

                supabase.table("sessions").update(update_payload).eq(
                    "id", session_id
                ).execute()

                try:
                    from memory.redis_client import redis_client

                    redis_client.set(
                        f"topic_change_notify:{chat_id}",
                        old_idea_text,
                        ex=300,
                    )
                except Exception as e:
                    logger.warning(
                        "[L0] Redis unavailable, skipping topic change "
                        "notification stash: %s",
                        e,
                    )

                return None

        return session

    except Exception as e:
        print(f"Error fetching active session for chat {chat_id}: {e}")
        return None


def create_session(
    ceo_id: str,
    chat_id: str,
    idea_text: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Inserts a new row into sessions table.

    Args:
        ceo_id: UUID of the CEO context
        chat_id: Chat ID
        idea_text: The CEO's first message text (for topic-change detection)

    Returns:
        Dict containing the created session row or None on failure
    """
    try:
        row = {
            "ceo_id": ceo_id,
            "chat_id": chat_id,
            "state": "NEEDS_CLARIFICATION",
            "awaiting_research": False,
        }
        if idea_text and not idea_text.startswith("Previous idea ("):
            row["idea_text"] = idea_text[:200]

        response = (
            supabase.table("sessions")
            .insert(row)
            .execute()
        )

        if response.data and len(response.data) > 0:
            return response.data[0]
        return None

    except Exception as e:
        if "idea_text" in str(e) and idea_text:
            logger.warning(
                "[SESSION] idea_text column not available yet, "
                "creating session without it"
            )
            row_without = {
                "ceo_id": ceo_id,
                "chat_id": chat_id,
                "state": "NEEDS_CLARIFICATION",
                "awaiting_research": False,
            }
            try:
                response = (
                    supabase.table("sessions")
                    .insert(row_without)
                    .execute()
                )
                if response.data and len(response.data) > 0:
                    return response.data[0]
            except Exception as inner_e:
                logger.error(
                    "Error creating session (retry): %s", inner_e
                )
            return None
        logger.error(
            "Error creating session for chat %s: %s",
            chat_id,
            e,
        )
        return None


def update_session_state(session_id: str, new_state: str) -> Optional[Dict[str, Any]]:
    """
    Updates the state field on the sessions table.

    Args:
        session_id: UUID of the session
        new_state: New state value (must be one of the allowed session states)

    Returns:
        Dict containing the updated session row or None on failure
    """
    try:
        response = (
            supabase.table("sessions")
            .update({"state": new_state})
            .eq("id", session_id)
            .execute()
        )

        if response.data and len(response.data) > 0:
            return response.data[0]
        return None

    except Exception as e:
        print(f"Error updating session {session_id} to state {new_state}: {e}")
        return None


import uuid as _uuid

_SESSION_UUID_NAMESPACE = _uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")  # DNS namespace


def coerce_session_uuid(session_id) -> Optional[str]:
    """Return a valid UUID string for a session identifier.

    events_logs/messages.session_id are UUID columns, but session keys are often
    non-UUID strings (web session keys, telegram chat ids, test ids). Passing
    those raised 'invalid input syntax for type uuid' and dropped the log. Real
    UUIDs pass through unchanged; any other string maps deterministically via
    uuid5 so the same session key always yields the same UUID (correlation kept).
    """
    if session_id is None:
        return None
    s = str(session_id)
    try:
        return str(_uuid.UUID(s))
    except (ValueError, AttributeError, TypeError):
        return str(_uuid.uuid5(_SESSION_UUID_NAMESPACE, s))


def log_event(
    agent_id: str,
    action: str,
    session_id: str,
    state_before: Optional[str] = None,
    state_after: Optional[str] = None,
    input_ref: Optional[str] = None,
    output_ref: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Inserts a row into events_logs table.

    Args:
        agent_id: ID of the agent performing the action
        action: Description of the action
        session_id: UUID of the session
        state_before: State before the action (optional)
        state_after: State after the action (optional)
        input_ref: Reference to input data (optional)
        output_ref: Reference to output data (optional)

    Returns:
        Dict containing the inserted event log row or None on failure
    """
    try:
        response = (
            supabase.table("events_logs")
            .insert({
                "agent_id": agent_id,
                "action": action,
                "session_id": coerce_session_uuid(session_id),
                "state_before": state_before,
                "state_after": state_after,
                "input_ref": input_ref,
                "output_ref": output_ref
            })
            .execute()
        )

        if response.data and len(response.data) > 0:
            return response.data[0]
        return None

    except Exception as e:
        # events_logs.session_id has a FK to sessions; a session key with no
        # sessions row (web-default, synthetic, test) would drop the log. Never
        # lose the event — retry unlinked (session_id=None) and note the origin.
        try:
            response = (
                supabase.table("events_logs")
                .insert({
                    "agent_id": agent_id,
                    "action": action,
                    "session_id": None,
                    "state_before": state_before,
                    "state_after": state_after,
                    "input_ref": (f"[session:{session_id}] {input_ref}" if input_ref
                                  else f"[session:{session_id}]"),
                    "output_ref": output_ref,
                })
                .execute()
            )
            return response.data[0] if response.data else None
        except Exception as e2:
            logger.error("Error logging event for agent %s: %s / %s", agent_id, e, e2)
            return None


import hashlib as _hashlib


def _message_id_to_bigint(message_id) -> Optional[int]:
    """Map any message id to a stable BIGINT for the dedup column.

    The messages.message_id column is BIGINT, but web ids are strings
    ("web_<uuid>"). Numeric ids pass through; anything else hashes to a stable
    positive bigint so dedup works channel-agnostically without a schema change.
    """
    if message_id is None:
        return None
    try:
        return int(message_id)
    except (ValueError, TypeError):
        digest = _hashlib.sha1(str(message_id).encode()).hexdigest()
        return int(digest[:15], 16)  # < 2^60, safely within BIGINT


def log_message(
    message_id,
    content: str,
    session_id: str,
    channel: str = "web",
    message_id=None,
) -> Optional[Dict[str, Any]]:
    """
    Inserts a row into messages table, deduplicating on the message id.

    Web is the only channel. `message_id` is the unique per-message id (e.g.
    "web_<uuid>"); it is stored in the (legacy-named) message_id column and used
    for dedup regardless of channel. `message_id` is accepted only as a
    backward-compatible alias.

    Args:
        message_id: Unique message id (any channel).
        content: Message content.
        session_id: UUID of the session.
        channel: Channel label (defaults to 'web').

    Returns:
        Dict of the inserted row, or None if duplicate or on failure.
    """
    if message_id is None:
        message_id = message_id
    try:
        # Dedup on the message id for ANY channel. Previously this only ran for
        # channel=='telegram' AND the caller passed the wrong kwarg, so the id
        # was never stored and duplicates were never detected.
        if message_id is not None and check_message_exists(message_id):
            logger.info("Message %s already exists (duplicate) — skipping", message_id)
            return None

        row = {
            "content": content,
            "session_id": session_id,
            "channel": channel,
        }
        if message_id is not None:
            row["message_id"] = _message_id_to_bigint(message_id)  # legacy col = external msg id

        response = (
            supabase.table("messages")
            .insert(row)
            .execute()
        )

        if response.data and len(response.data) > 0:
            return response.data[0]
        return None

    except Exception as e:
        logger.error("Error logging message %s: %s", message_id, e)
        return None


def check_message_exists(message_id) -> bool:
    """
    Checks if a message with this id already exists (dedup), any channel.

    Args:
        message_id: Unique message id.

    Returns:
        True if a message with this id exists, False otherwise.
    """
    if message_id is None:
        return False
    try:
        response = (
            supabase.table("messages")
            .select("id")
            .eq("message_id", _message_id_to_bigint(message_id))
            .limit(1)
            .execute()
        )
        return bool(response.data and len(response.data) > 0)

    except Exception as e:
        logger.error("Error checking if message %s exists: %s", message_id, e)
        return False


def clear_session_assumptions(session_id: str) -> bool:
    """
    Mark all assumptions for a session as inactive.
    Used when the CEO chooses 'Adjust' to reset the question counter.

    Args:
        session_id: UUID of the session

    Returns:
        True if successful, False otherwise
    """
    try:
        response = (
            supabase.table("assumptions")
            .update({"status": "inactive"})
            .eq("session_id", session_id)
            .eq("status", "active")
            .execute()
        )
        return True
    except Exception as e:
        print(f"Error clearing assumptions for session {session_id}: {e}")
        return False


def get_messages_for_session(session_id: str) -> list:
    """
    Gets all messages for a specific session, ordered chronologically.

    Args:
        session_id: UUID of the session

    Returns:
        List of message dicts
    """
    try:
        response = (
            supabase.table("messages")
            .select("*")
            .eq("session_id", session_id)
            .order("received_at", desc=False)
            .execute()
        )
        return response.data if response.data else []

    except Exception as e:
        print(f"Error fetching messages for session {session_id}: {e}")
        return []


# Valid business_plan_sections.status values (mirrors the DB CHECK constraint).
# 'approved' is the terminal state of the governance chain and must be earned,
# never assigned by default — see set_section_status().
VALID_SECTION_STATUSES = (
    "not_started",
    "in_progress",
    "blocked",
    "ready_for_draft",
    "approved",
)

# Ordering for open sections: most-actionable first, so callers that show only
# the top few (e.g. L1's `open_sections[:5]`) surface sections actually being
# worked rather than an arbitrary slice of not_started nodes.
_SECTION_STATUS_PRIORITY = {
    "in_progress": 0,
    "blocked": 1,
    "ready_for_draft": 2,
    "not_started": 3,
}


def _sort_open_sections(sections: list) -> list:
    """Order open sections most-actionable first, then by section_id."""
    return sorted(
        sections,
        key=lambda s: (
            _SECTION_STATUS_PRIORITY.get(s.get("status"), 99),
            s.get("section_id") or "",
        ),
    )


def get_open_business_plan_sections(session_id: Optional[str] = None) -> list:
    """
    Gets business plan sections that are NOT approved, most-actionable first.

    If session_id is provided, only returns sections referenced by decisions
    in that session (plus any not_started sections for planning context).

    Args:
        session_id: If provided, scope to sections relevant to this session.

    Returns:
        List of business plan section dicts, ordered so in_progress and
        session-relevant sections come before untouched not_started ones.
    """
    try:
        if session_id:
            decisions_resp = (
                supabase.table("decisions")
                .select("sections_affected")
                .eq("session_id", session_id)
                .execute()
            )
            session_section_ids = set()
            if decisions_resp.data:
                for d in decisions_resp.data:
                    for sid in (d.get("sections_affected") or []):
                        session_section_ids.add(sid)

            response = (
                supabase.table("business_plan_sections")
                .select("*")
                .neq("status", "approved")
                .execute()
            )
            if not response.data:
                return []

            scoped = [
                s for s in response.data
                if s.get("section_id") in session_section_ids
                or s.get("status") in ("in_progress", "not_started")
            ]
            return _sort_open_sections(scoped)

        response = (
            supabase.table("business_plan_sections")
            .select("*")
            .neq("status", "approved")
            .execute()
        )

        return _sort_open_sections(response.data) if response.data else []

    except Exception as e:
        logger.error("Error fetching open business plan sections: %s", e)
        return []


def set_section_status(
    section_id: str,
    new_status: str,
    controller_approved: bool = False,
    reason: Optional[str] = None,
) -> bool:
    """Set a business_plan_sections node's status — the only sanctioned writer.

    Enforces the governance rule that AI cannot approve a section on its own:
    moving a node to 'approved' requires an explicit controller decision, passed
    as controller_approved=True. Any other status is a normal workflow update.

    Args:
        section_id: The BP node ID (e.g. "BP.1.1").
        new_status: One of VALID_SECTION_STATUSES.
        controller_approved: Must be True to set status='approved'. Ignored for
            every other status.
        reason: Optional human-readable note for the log (why the transition).

    Returns:
        True if the row was updated, False otherwise.
    """
    if new_status not in VALID_SECTION_STATUSES:
        logger.error(
            "Refusing to set section %s to invalid status %r (valid: %s)",
            section_id,
            new_status,
            ", ".join(VALID_SECTION_STATUSES),
        )
        return False

    if new_status == "approved" and not controller_approved:
        logger.error(
            "Refusing to approve section %s without a controller decision "
            "(call set_section_status(..., controller_approved=True))",
            section_id,
        )
        return False

    try:
        result = (
            supabase.table("business_plan_sections")
            .update({"status": new_status})
            .eq("section_id", section_id)
            .execute()
        )
        if not result.data:
            logger.warning(
                "set_section_status: no row matched section_id=%s", section_id
            )
            return False
        logger.info(
            "Section %s -> %s%s",
            section_id,
            new_status,
            f" ({reason})" if reason else "",
        )
        return True
    except Exception as e:
        logger.error("Error setting status for section %s: %s", section_id, e)
        return False


def get_unresolved_assumptions(session_id: Optional[str] = None) -> list:
    """
    Gets assumptions with clarification_status = 'pending' or 'assumed_not_clarified'.

    Args:
        session_id: If provided, only return assumptions for this session.
                    If None, returns all (for backward compat with dashboard).

    Returns:
        List of assumption dicts
    """
    try:
        query = (
            supabase.table("assumptions")
            .select("*")
            .in_("clarification_status", ["pending", "assumed_not_clarified"])
            .eq("status", "active")
        )

        if session_id:
            query = query.eq("session_id", session_id)

        response = query.execute()
        return response.data if response.data else []

    except Exception as e:
        print(f"Error fetching unresolved assumptions: {e}")
        return []


def get_pending_decisions(session_id: Optional[str] = None) -> list:
    """
    Gets decisions with status = 'pending_approval'.

    Args:
        session_id: If provided, only return decisions for this session.

    Returns:
        List of decision dicts
    """
    try:
        query = (
            supabase.table("decisions")
            .select("*")
            .eq("status", "pending_approval")
        )

        if session_id:
            query = query.eq("session_id", session_id)

        response = query.execute()
        return response.data if response.data else []

    except Exception as e:
        print(f"Error fetching pending decisions: {e}")
        return []


def create_assumption(
    assumption_id: str,
    statement: str,
    session_id: str,
    confidence: str = "low",
    clarification_status: str = "pending",
    based_on: Optional[list] = None,
    affects: Optional[list] = None
) -> Optional[Dict[str, Any]]:
    """
    Creates a new assumption in the assumptions table.

    Args:
        assumption_id: Unique identifier for the assumption
        statement: The assumption statement
        session_id: UUID of the session
        confidence: Confidence level (low, medium, high)
        clarification_status: Clarification status (pending, clarified, assumed_not_clarified)
        based_on: List of references this assumption is based on
        affects: List of sections/decisions this affects

    Returns:
        Dict containing the created assumption or None on failure
    """
    try:
        response = (
            supabase.table("assumptions")
            .insert({
                "assumption_id": assumption_id,
                "statement": statement,
                "session_id": session_id,
                "confidence": confidence,
                "clarification_status": clarification_status,
                "based_on": based_on or [],
                "affects": affects or [],
                "status": "active"
            })
            .execute()
        )

        if response.data and len(response.data) > 0:
            return response.data[0]
        return None

    except Exception as e:
        print(f"Error creating assumption {assumption_id}: {e}")
        return None


def get_research_briefs_for_session(session_id: str) -> list:
    """
    Gets all research briefs for a specific session.

    Args:
        session_id: UUID of the session

    Returns:
        List of research brief dicts
    """
    try:
        response = (
            supabase.table("research_briefs")
            .select("*")
            .eq("session_id", session_id)
            .order("created_at", desc=True)
            .execute()
        )

        return response.data if response.data else []

    except Exception as e:
        print(f"Error fetching research briefs for session {session_id}: {e}")
        return []


def get_latest_research_brief() -> Optional[Dict[str, Any]]:
    """
    Gets the most recent research brief across all sessions.

    Returns:
        Dict containing the latest research brief or None
    """
    try:
        response = (
            supabase.table("research_briefs")
            .select("*")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )

        if response.data and len(response.data) > 0:
            return response.data[0]
        return None

    except Exception as e:
        print(f"Error fetching latest research brief: {e}")
        return None


def get_assumptions_for_session(session_id: str) -> list:
    """
    Gets all active assumptions for a specific session.

    Args:
        session_id: UUID of the session

    Returns:
        List of assumption dicts
    """
    try:
        response = (
            supabase.table("assumptions")
            .select("*")
            .eq("session_id", session_id)
            .eq("status", "active")
            .execute()
        )

        return response.data if response.data else []

    except Exception as e:
        print(f"Error fetching assumptions for session {session_id}: {e}")
        return []


def get_decisions_for_session(session_id: str) -> list:
    """
    Gets all decisions for a specific session.

    Args:
        session_id: UUID of the session

    Returns:
        List of decision dicts
    """
    try:
        response = (
            supabase.table("decisions")
            .select("*")
            .eq("session_id", session_id)
            .order("created_at", desc=True)
            .execute()
        )

        return response.data if response.data else []

    except Exception as e:
        print(f"Error fetching decisions for session {session_id}: {e}")
        return []


def create_decision(
    decision_id: str,
    decision: str,
    rationale: str,
    session_id: str,
    assumptions_used: Optional[list] = None,
    evidence_used: Optional[list] = None,
    sections_affected: Optional[list] = None,
    status: str = "pending_approval"
) -> Optional[Dict[str, Any]]:
    """
    Creates a new decision in the decisions table.

    Args:
        decision_id: Unique identifier for the decision
        decision: The decision statement
        rationale: Explanation/reasoning for the decision
        session_id: UUID of the session
        assumptions_used: List of assumption IDs this decision is based on
        evidence_used: List of research IDs used as evidence
        sections_affected: List of section IDs affected by this decision
        status: Decision status (pending_approval, approved, rejected, superseded)

    Returns:
        Dict containing the created decision or None on failure
    """
    try:
        response = (
            supabase.table("decisions")
            .insert({
                "decision_id": decision_id,
                "decision": decision,
                "rationale": rationale,
                "session_id": session_id,
                "assumptions_used": assumptions_used or [],
                "evidence_used": evidence_used or [],
                "sections_affected": sections_affected or [],
                "status": status,
                "version": 1
            })
            .execute()
        )

        if response.data and len(response.data) > 0:
            return response.data[0]
        return None

    except Exception as e:
        print(f"Error creating decision {decision_id}: {e}")
        return None


def save_agent_output(
    agent_id: str,
    session_id: str,
    output_text: str,
    input_summary: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Saves agent output to the agent_outputs table.

    Args:
        agent_id: ID of the agent (e.g., "L3_FEEDBACK_AGENT")
        session_id: UUID of the session
        output_text: The full output text from the agent
        input_summary: Optional summary of the input

    Returns:
        Dict containing the saved output or None on failure
    """
    try:
        response = (
            supabase.table("agent_outputs")
            .insert({
                "agent_id": agent_id,
                "session_id": session_id,
                "output_text": output_text,
                "input_summary": input_summary
            })
            .execute()
        )

        if response.data and len(response.data) > 0:
            return response.data[0]
        return None

    except Exception as e:
        print(f"Error saving agent output for {agent_id}: {e}")
        return None


def update_decision_status(decision_id: str, new_status: str) -> Optional[Dict[str, Any]]:
    """
    Updates the status of a decision.

    Args:
        decision_id: Unique identifier for the decision
        new_status: New status (pending_approval, approved, rejected, superseded)

    Returns:
        Dict containing the updated decision or None on failure
    """
    try:
        response = (
            supabase.table("decisions")
            .update({"status": new_status})
            .eq("decision_id", decision_id)
            .execute()
        )

        if response.data and len(response.data) > 0:
            return response.data[0]
        return None

    except Exception as e:
        print(f"Error updating decision {decision_id} to status {new_status}: {e}")
        return None


def update_business_plan_section_status(section_id: str, new_status: str) -> Optional[Dict[str, Any]]:
    """
    Updates the status of a business plan section.

    Args:
        section_id: Unique identifier for the section
        new_status: New status (not_started, in_progress, blocked, ready_for_draft, approved)

    Returns:
        Dict containing the updated section or None on failure
    """
    try:
        response = (
            supabase.table("business_plan_sections")
            .update({"status": new_status})
            .eq("section_id", section_id)
            .execute()
        )

        if response.data and len(response.data) > 0:
            return response.data[0]
        return None

    except Exception as e:
        print(f"Error updating section {section_id} to status {new_status}: {e}")
        return None


# ============================================================================
# MEMORY PROFILE FUNCTIONS
# ============================================================================

def get_memory_profile(
    ceo_id: str,
    exclude_session_id: Optional[str] = None,
) -> list:
    """
    Get all memory entries for a CEO, ordered by most recently referenced.

    Args:
        ceo_id: UUID of the CEO
        exclude_session_id: If provided, exclude memories sourced from this
                            session (used when a session was just closed due
                            to topic change, to avoid loading stale context).

    Returns:
        List of memory entries (dicts)
    """
    try:
        query = (
            supabase.table("memory_profile")
            .select("*")
            .eq("ceo_id", ceo_id)
            .order("last_referenced_at", desc=True)
        )

        if exclude_session_id:
            query = query.neq("source_session_id", exclude_session_id)

        response = query.execute()
        return response.data if response.data else []

    except Exception as e:
        print(f"Error fetching memory profile for CEO {ceo_id}: {e}")
        return []


def add_memory(
    ceo_id: str,
    memory_type: str,
    content: str,
    source_session_id: str,
    confidence: str = "medium"
) -> Optional[Dict[str, Any]]:
    """
    Add a new memory entry to the memory profile.

    Args:
        ceo_id: UUID of the CEO
        memory_type: Type of memory (strategic_decision, recurring_priority, etc.)
        content: The memory content
        source_session_id: UUID of the session this memory came from
        confidence: Confidence level (low, medium, high)

    Returns:
        Dict with the created memory or None if failed
    """
    try:
        memory_data = {
            "ceo_id": ceo_id,
            "memory_type": memory_type,
            "content": content,
            "source_session_id": source_session_id,
            "confidence": confidence
        }

        response = supabase.table("memory_profile").insert(memory_data).execute()

        if response.data and len(response.data) > 0:
            print(f"[MEMORY] Created {memory_type}: {content[:50]}...")
            return response.data[0]
        return None

    except Exception as e:
        print(f"Error adding memory: {e}")
        return None


def update_memory_last_referenced(memory_id: str) -> bool:
    """
    Update the last_referenced_at timestamp for a memory.

    Args:
        memory_id: UUID of the memory entry

    Returns:
        True if successful, False otherwise
    """
    try:
        from datetime import datetime

        response = (
            supabase.table("memory_profile")
            .update({"last_referenced_at": datetime.now().isoformat()})
            .eq("id", memory_id)
            .execute()
        )

        return response.data is not None and len(response.data) > 0

    except Exception as e:
        print(f"Error updating memory last_referenced: {e}")
        return False


def get_recent_sessions(ceo_id: str, limit: int = 5) -> list:
    """
    Get the most recent completed sessions for a CEO with their decisions and assumptions.

    Args:
        ceo_id: UUID of the CEO
        limit: Maximum number of sessions to return

    Returns:
        List of session dicts with embedded decisions and assumptions
    """
    try:
        # First get recent sessions
        sessions_response = (
            supabase.table("sessions")
            .select("*")
            .eq("ceo_id", ceo_id)
            .order("started_at", desc=True)
            .limit(limit)
            .execute()
        )

        if not sessions_response.data:
            return []

        sessions = sessions_response.data

        # Enrich each session with its decisions and assumptions
        for session in sessions:
            session_id = session.get("id")

            # Get decisions for this session
            decisions_response = (
                supabase.table("decisions")
                .select("*")
                .eq("session_id", session_id)
                .execute()
            )
            session["decisions"] = decisions_response.data if decisions_response.data else []

            # Get assumptions for this session
            assumptions_response = (
                supabase.table("assumptions")
                .select("*")
                .eq("session_id", session_id)
                .execute()
            )
            session["assumptions"] = assumptions_response.data if assumptions_response.data else []

        return sessions

    except Exception as e:
        print(f"Error fetching recent sessions: {e}")
        return []


def get_last_message_time(chat_id: int) -> Optional[str]:
    """
    Get the timestamp of the last message from this CEO.

    Args:
        chat_id: Telegram chat ID

    Returns:
        ISO timestamp string or None
    """
    try:
        # First get all sessions for this chat
        sessions_response = (
            supabase.table("sessions")
            .select("id")
            .eq("chat_id", chat_id)
            .execute()
        )

        if not sessions_response.data:
            return None

        # Get session IDs
        session_ids = [s.get("id") for s in sessions_response.data]

        # Now get the most recent message from any of these sessions
        response = (
            supabase.table("messages")
            .select("received_at")
            .in_("session_id", session_ids)
            .order("received_at", desc=True)
            .limit(1)
            .execute()
        )

        if response.data and len(response.data) > 0:
            return response.data[0].get("received_at")
        return None

    except Exception as e:
        print(f"Error getting last message time: {e}")
        return None


class SupabaseClient:
    """Wrapper class for Phase 2 agents that need object-oriented access."""

    def __init__(self):
        self.client = supabase
