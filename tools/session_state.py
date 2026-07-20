"""
Session State Management Helper

Provides utilities for managing session state stored in Supabase
(replaces previous Redis-based temporary state management).

All session temporary flags and data now live in sessions table.
"""

import logging
from typing import Optional, Any
from memory.supabase_client import (
    get_session_flag,
    set_session_flag,
    get_session_data,
    set_session_data,
    get_active_session,
)

logger = logging.getLogger(__name__)


class SessionStateManager:
    """Helper for managing session flags and temporary data."""

    @staticmethod
    async def set_challenge_pending(session_id: str, task_id: str) -> bool:
        """Mark that CEO challenge response is pending."""
        return set_session_data(session_id, "challenge_pending_task_id", task_id)

    @staticmethod
    async def get_challenge_pending(session_id: str) -> Optional[str]:
        """Get pending challenge task ID if any."""
        return get_session_data(session_id, "challenge_pending_task_id")

    @staticmethod
    async def clear_challenge_pending(session_id: str) -> bool:
        """Clear challenge pending marker."""
        return set_session_data(session_id, "challenge_pending_task_id", None)

    @staticmethod
    async def set_adjust_pending(session_id: str, task_id: str) -> bool:
        """Mark that CEO adjust response is pending."""
        return set_session_data(session_id, "adjust_pending_task_id", task_id)

    @staticmethod
    async def get_adjust_pending(session_id: str) -> Optional[str]:
        """Get pending adjust task ID if any."""
        return get_session_data(session_id, "adjust_pending_task_id")

    @staticmethod
    async def clear_adjust_pending(session_id: str) -> bool:
        """Clear adjust pending marker."""
        return set_session_data(session_id, "adjust_pending_task_id", None)

    @staticmethod
    async def set_gate2_active(session_id: str, is_active: bool) -> bool:
        """Mark Gate 2 as active or inactive."""
        return set_session_flag(session_id, "gate2_active", is_active)

    @staticmethod
    async def is_gate2_active(session_id: str) -> bool:
        """Check if Gate 2 listener is active."""
        return get_session_flag(session_id, "gate2_active", False)

    @staticmethod
    async def set_awaiting_clarification(session_id: str, is_waiting: bool) -> bool:
        """Mark session as awaiting CEO clarification."""
        return set_session_flag(session_id, "awaiting_clarification", is_waiting)

    @staticmethod
    async def is_awaiting_clarification(session_id: str) -> bool:
        """Check if session is awaiting clarification."""
        return get_session_flag(session_id, "awaiting_clarification", False)

    @staticmethod
    async def set_last_question(session_id: str, question: str) -> bool:
        """Store last question asked to CEO."""
        return set_session_data(session_id, "last_question", question)

    @staticmethod
    async def get_last_question(session_id: str) -> Optional[str]:
        """Get last question asked to CEO."""
        return get_session_data(session_id, "last_question")

    @staticmethod
    async def set_clarification_data(session_id: str, task_id: str, run_id: str) -> bool:
        """Store clarification context for routing response back."""
        return set_session_data(
            session_id,
            "clarification_data",
            {"task_id": task_id, "run_id": run_id}
        )

    @staticmethod
    async def get_clarification_data(session_id: str) -> Optional[dict]:
        """Get stored clarification context."""
        return get_session_data(session_id, "clarification_data")

    @staticmethod
    async def clear_clarification_data(session_id: str) -> bool:
        """Clear stored clarification context."""
        return set_session_data(session_id, "clarification_data", None)


# Global instance
session_state = SessionStateManager()
