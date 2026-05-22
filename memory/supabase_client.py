"""
Supabase Client for Multi-Agent AI System
Provides database operations for CEO context, sessions, messages, and event logging.
"""

import os
from typing import Optional, Dict, Any
from pathlib import Path
from dotenv import load_dotenv
from supabase import create_client, Client

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


def get_active_session(telegram_chat_id: int) -> Optional[Dict[str, Any]]:
    """
    Finds the most recent session where state is NOT 'COMPLETED'.

    Args:
        telegram_chat_id: Telegram chat ID to filter by

    Returns:
        Dict containing session data or None if no active session found
    """
    try:
        response = (
            supabase.table("sessions")
            .select("*")
            .eq("telegram_chat_id", telegram_chat_id)
            .neq("state", "COMPLETED")
            .order("started_at", desc=True)
            .limit(1)
            .execute()
        )

        if response.data and len(response.data) > 0:
            return response.data[0]
        return None

    except Exception as e:
        print(f"Error fetching active session for chat {telegram_chat_id}: {e}")
        return None


def create_session(ceo_id: str, telegram_chat_id: int) -> Optional[Dict[str, Any]]:
    """
    Inserts a new row into sessions table.

    Args:
        ceo_id: UUID of the CEO context
        telegram_chat_id: Telegram chat ID

    Returns:
        Dict containing the created session row or None on failure
    """
    try:
        response = (
            supabase.table("sessions")
            .insert({
                "ceo_id": ceo_id,
                "telegram_chat_id": telegram_chat_id,
                "state": "NEEDS_CLARIFICATION",
                "awaiting_research": False
            })
            .execute()
        )

        if response.data and len(response.data) > 0:
            return response.data[0]
        return None

    except Exception as e:
        print(f"Error creating session for chat {telegram_chat_id}: {e}")
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
                "session_id": session_id,
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
        print(f"Error logging event for agent {agent_id}: {e}")
        return None


def log_message(
    telegram_message_id,
    content: str,
    session_id: str,
    channel: str = "telegram"
) -> Optional[Dict[str, Any]]:
    """
    Inserts a row into messages table.

    Args:
        telegram_message_id: Unique Telegram message ID (int) or web message ID (str)
        content: Message content
        session_id: UUID of the session
        channel: 'telegram' or 'web'

    Returns:
        Dict containing the inserted message row or None if duplicate or on failure
    """
    try:
        row = {
            "content": content,
            "session_id": session_id,
            "channel": channel,
        }

        if channel == "telegram":
            if check_message_exists(telegram_message_id):
                print(f"Message {telegram_message_id} already exists (duplicate)")
                return None
            row["telegram_message_id"] = telegram_message_id
        # Web messages have no telegram_message_id — leave it NULL

        response = (
            supabase.table("messages")
            .insert(row)
            .execute()
        )

        if response.data and len(response.data) > 0:
            return response.data[0]
        return None

    except Exception as e:
        print(f"Error logging message {telegram_message_id}: {e}")
        return None


def check_message_exists(telegram_message_id: int) -> bool:
    """
    Checks if a message with this telegram_message_id already exists.

    Args:
        telegram_message_id: Unique Telegram message ID

    Returns:
        True if message exists, False otherwise
    """
    try:
        response = (
            supabase.table("messages")
            .select("id")
            .eq("telegram_message_id", telegram_message_id)
            .limit(1)
            .execute()
        )

        exists = bool(response.data and len(response.data) > 0)
        return exists

    except Exception as e:
        print(f"Error checking if message {telegram_message_id} exists: {e}")
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


def get_open_business_plan_sections() -> list:
    """
    Gets all business plan sections that are NOT approved.

    Returns:
        List of business plan section dicts
    """
    try:
        response = (
            supabase.table("business_plan_sections")
            .select("*")
            .neq("status", "approved")
            .execute()
        )

        return response.data if response.data else []

    except Exception as e:
        print(f"Error fetching open business plan sections: {e}")
        return []


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


def get_pending_decisions() -> list:
    """
    Gets all decisions with status = 'pending_approval'.

    Returns:
        List of decision dicts
    """
    try:
        response = (
            supabase.table("decisions")
            .select("*")
            .eq("status", "pending_approval")
            .execute()
        )

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

def get_memory_profile(ceo_id: str) -> list:
    """
    Get all memory entries for a CEO, ordered by most recently referenced.

    Args:
        ceo_id: UUID of the CEO

    Returns:
        List of memory entries (dicts)
    """
    try:
        response = (
            supabase.table("memory_profile")
            .select("*")
            .eq("ceo_id", ceo_id)
            .order("last_referenced_at", desc=True)
            .execute()
        )

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


def get_last_message_time(telegram_chat_id: int) -> Optional[str]:
    """
    Get the timestamp of the last message from this CEO.

    Args:
        telegram_chat_id: Telegram chat ID

    Returns:
        ISO timestamp string or None
    """
    try:
        # First get all sessions for this chat
        sessions_response = (
            supabase.table("sessions")
            .select("id")
            .eq("telegram_chat_id", telegram_chat_id)
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
