"""
L0 Input Guard Agent
First checkpoint for all incoming messages.
Validates sender, checks for duplicates, manages sessions, and logs events.
"""

from typing import Dict, Any, Optional
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from memory.supabase_client import (
    get_ceo_context,
    check_message_exists,
    get_active_session,
    create_session,
    log_message,
    log_event
)
from tools.trace_emitter import emit_trace


def validate_message(message_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    L0 Input Guard: Validate incoming message and setup session.

    Args:
        message_data: Dict with message_id, chat_id, text, from_user

    Returns:
        Dict with:
            - valid (bool): Whether the message passed validation
            - session_id (str): UUID of the session (if valid)
            - ceo_id (str): UUID of the CEO (if valid)
            - is_new_session (bool): Whether a new session was created
            - reason (str): Reason for rejection (if invalid)
    """

    message_id = message_data.get("message_id")
    chat_id = message_data.get("chat_id")
    text = message_data.get("text")
    channel = message_data.get("channel", "telegram")
    from_user = message_data.get("from_user", {})

    session_key = str(chat_id)

    print(f"[L0] Processing message {message_id} from chat {chat_id}")

    # Step 1: Load CEO context
    emit_trace(session_key, "L0", "loading_ceo_context", "Loading CEO profile")
    ceo_context = get_ceo_context()

    if not ceo_context:
        print("[L0] ✗ No CEO context found in database")
        return {
            "valid": False,
            "session_id": None,
            "ceo_id": None,
            "is_new_session": False,
            "reason": "No CEO context configured in system"
        }

    ceo_id = ceo_context.get("id")
    ceo_telegram_chat_id = ceo_context.get("telegram_chat_id")

    # Step 2: Validate sender is the CEO
    emit_trace(session_key, "L0", "validating_sender", "Checking sender identity")
    if ceo_telegram_chat_id is None:
        print("[L0] ✗ CEO telegram_chat_id not configured in database")
        return {
            "valid": False,
            "session_id": None,
            "ceo_id": ceo_id,
            "is_new_session": False,
            "reason": "CEO telegram_chat_id not configured. Cannot validate sender."
        }

    if ceo_telegram_chat_id != chat_id:
        print(f"[L0] ✗ Unauthorized sender: {chat_id} (expected: {ceo_telegram_chat_id})")
        return {
            "valid": False,
            "session_id": None,
            "ceo_id": ceo_id,
            "is_new_session": False,
            "reason": f"Unauthorized sender (chat_id: {chat_id}). Only CEO can send messages."
        }

    print(f"[L0] ✓ Sender validated: CEO {ceo_context.get('name')}")

    # Step 3: Check for duplicate message (only for Telegram — web messages are unique by UUID)
    emit_trace(session_key, "L0", "duplicate_check", "Checking for duplicate message")
    if channel == "telegram" and check_message_exists(message_id):
        print(f"[L0] ✗ Duplicate message detected: {message_id}")
        return {
            "valid": False,
            "session_id": None,
            "ceo_id": ceo_id,
            "is_new_session": False,
            "reason": f"Duplicate message (message_id: {message_id} already processed)"
        }

    print(f"[L0] ✓ Message is unique: {message_id}")

    # Step 4: Get or create active session
    emit_trace(session_key, "L0", "get_session", "Loading or creating session")
    session = get_active_session(chat_id)
    is_new_session = False

    if session:
        session_id = session.get("id")
        print(f"[L0] ✓ Using existing session: {session_id}")
    else:
        # Create new session
        session = create_session(ceo_id, chat_id)
        if not session:
            print("[L0] ✗ Failed to create new session")
            return {
                "valid": False,
                "session_id": None,
                "ceo_id": ceo_id,
                "is_new_session": False,
                "reason": "Failed to create session in database"
            }

        session_id = session.get("id")
        is_new_session = True
        print(f"[L0] ✓ Created new session: {session_id}")

    # Step 5: Log the raw message
    emit_trace(session_key, "L0", "logging_message", "Logging raw message to DB")
    message_log = log_message(
        telegram_message_id=message_id,
        content=text,
        session_id=session_id,
        channel=channel
    )

    if not message_log:
        print(f"[L0] ✗ Failed to log message {message_id}")
        return {
            "valid": False,
            "session_id": session_id,
            "ceo_id": ceo_id,
            "is_new_session": is_new_session,
            "reason": "Failed to log message in database"
        }

    print(f"[L0] ✓ Message logged: {message_id}")

    # Step 6: Log the event
    event_log = log_event(
        agent_id="L0_INPUT_GUARD",
        action=f"VALIDATED_MESSAGE: {text[:50]}..." if len(text) > 50 else f"VALIDATED_MESSAGE: {text}",
        session_id=session_id,
        state_before=None if is_new_session else session.get("state"),
        state_after=session.get("state"),
        input_ref=f"message_id:{message_id}",
        output_ref=f"session_id:{session_id}"
    )

    if not event_log:
        print(f"[L0] ⚠ Warning: Failed to log event for message {message_id}")
        # Continue anyway - event logging failure shouldn't block processing

    print(f"[L0] ✓ Event logged for session {session_id}")

    # Success!
    emit_trace(session_key, "L0", "complete", "Message validated and routed", {"session_id": session_id})
    print(f"[L0] ✅ Message validated successfully")
    return {
        "valid": True,
        "session_id": session_id,
        "ceo_id": ceo_id,
        "is_new_session": is_new_session,
        "reason": None
    }


if __name__ == "__main__":
    # Test the L0 agent
    test_message = {
        "message_id": 999999,
        "chat_id": 8866294087,
        "text": "Test message for L0 agent",
        "from_user": {
            "id": 8866294087,
            "username": "test_user",
            "first_name": "Test"
        }
    }

    result = validate_message(test_message)
    print("\n" + "=" * 60)
    print("Test Result:")
    print("=" * 60)
    for key, value in result.items():
        print(f"  {key}: {value}")
    print("=" * 60)
