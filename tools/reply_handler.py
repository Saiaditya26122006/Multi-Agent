"""
Reply handler — sends agent responses to the web interface.
All agent code should call send_reply() instead of sending directly.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


async def send_reply(
    chat_id: int,
    text: str,
    reply_markup=None,
    channel: Optional[str] = None,
) -> bool:
    """Send a reply to the CEO via the web interface.

    Args:
        chat_id: Session key identifier.
        text: The message text to send.
        reply_markup: Optional keyboard/button data (rendered by web UI).
        channel: Ignored (web-only now).

    Returns:
        True if send succeeded.
    """
    session_key = str(chat_id)

    try:
        from web.server import manager

        payload = {
            "role": "assistant",
            "text": text,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "channel": "system",
        }
        if reply_markup:
            payload["buttons"] = reply_markup

        await manager.broadcast(session_key, payload)
        return True
    except Exception as e:
        logger.error("[Reply] WebSocket broadcast failed: %s", e)
        return False


def create_decision_keyboard():
    """Create Yes/Adjust/Kill decision buttons for the web UI."""
    return [
        {"text": "Yes", "callback_data": "decision_yes"},
        {"text": "Adjust", "callback_data": "decision_adjust"},
        {"text": "Kill", "callback_data": "decision_kill"},
    ]


def create_task_preview_keyboard(tasks: list) -> list:
    """Create task preview buttons for the web UI."""
    buttons = []
    for task in tasks[:5]:
        label = task.get("title", task.get("name", "Task"))[:30]
        buttons.append({"text": label, "callback_data": f"task_{task.get('id', '')}"})
    return buttons
