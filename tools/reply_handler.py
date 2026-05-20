"""
Unified reply handler — sends agent responses to both Telegram and Web.
All agent code should call send_reply() instead of send_message() directly.
"""

import logging
from datetime import datetime
from typing import Optional

from tools.telegram_handler import send_message as telegram_send_message

logger = logging.getLogger(__name__)


async def send_reply(
    chat_id: int,
    text: str,
    reply_markup=None,
    channel: Optional[str] = None,
) -> bool:
    """
    Send a reply to the CEO on all active channels.

    Args:
        chat_id: The Telegram chat ID (also used as web session_key)
        text: The message text to send
        reply_markup: Optional Telegram inline keyboard markup
        channel: If set, only send on this channel ('telegram' or 'web')

    Returns:
        True if at least one channel succeeded
    """
    success = False
    session_key = str(chat_id)

    if channel != "web":
        try:
            result = await telegram_send_message(chat_id, text, reply_markup=reply_markup)
            if result:
                success = True
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")

    if channel != "telegram":
        try:
            from web.server import manager

            await manager.broadcast(
                session_key,
                {
                    "role": "assistant",
                    "text": text,
                    "timestamp": datetime.utcnow().isoformat(),
                    "channel": "system",
                },
            )
            success = True
        except Exception as e:
            logger.error(f"WebSocket broadcast failed: {e}")

    if not success:
        logger.error(f"Failed to send reply to chat_id={chat_id} on any channel")

    return success
