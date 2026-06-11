"""
Telegram bot handler for multi-agent AI system.
"""

import os
import logging
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# Load environment variables
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(env_path)

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Get bot token
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

if not TELEGRAM_BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN not found in .env file")


async def send_message(chat_id: int, text: str, reply_markup=None) -> bool:
    """
    Send a message to a Telegram chat.

    Args:
        chat_id: The Telegram chat ID
        text: The message text to send
        reply_markup: Optional inline keyboard markup

    Returns:
        bool: True if message sent successfully, False otherwise
    """
    try:
        application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        await application.initialize()
        await application.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
        await application.shutdown()
        logger.info(f"Message sent to chat_id {chat_id}: {text[:50]}...")
        return True
    except Exception as e:
        logger.error(f"Failed to send message to chat_id {chat_id}: {e}")
        return False


async def send_document(chat_id: int, file_path: str, caption: str = None) -> bool:
    """
    Send a document file to a Telegram chat.

    Args:
        chat_id: The Telegram chat ID
        file_path: Path to the document file
        caption: Optional caption text

    Returns:
        bool: True if document sent successfully, False otherwise
    """
    try:
        application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        await application.initialize()

        with open(file_path, 'rb') as doc:
            await application.bot.send_document(
                chat_id=chat_id,
                document=doc,
                caption=caption or "📄 Business plan document ready",
                filename=Path(file_path).name
            )

        await application.shutdown()
        logger.info(f"Document sent to chat_id {chat_id}: {file_path}")
        return True
    except Exception as e:
        logger.error(f"Failed to send document to chat_id {chat_id}: {e}")
        return False


def create_decision_keyboard() -> InlineKeyboardMarkup:
    """
    Create an inline keyboard with Yes/Adjust/Kill buttons for decisions.

    Returns:
        InlineKeyboardMarkup: The keyboard markup
    """
    keyboard = [
        [
            InlineKeyboardButton("✅ Yes - proceed as planned", callback_data="decision_yes"),
            InlineKeyboardButton("🔧 Adjust - modify the approach", callback_data="decision_adjust"),
            InlineKeyboardButton("❌ Kill - stop this initiative", callback_data="decision_kill")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


async def _handle_message_wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, handle_message_callback):
    """
    Internal wrapper to handle incoming Telegram messages.

    Args:
        update: The Telegram update object
        context: The context object
        handle_message_callback: The callback function to process the message
    """
    if update.message and update.message.text:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        message_data = {
            "message_id": update.message.message_id,
            "chat_id": update.message.chat_id,
            "text": update.message.text,
            "from_user": {
                "id": update.message.from_user.id,
                "username": update.message.from_user.username,
                "first_name": update.message.from_user.first_name,
            }
        }

        logger.info(f"[{timestamp}] Received message from {message_data['from_user']['username']} (chat_id: {message_data['chat_id']}): {message_data['text']}")

        # Check if this is a Gate 2 response (Phase 2 pipeline approval)
        if handle_gate2_response(message_data["chat_id"], message_data["text"]):
            await send_message(message_data["chat_id"], "Got it. Running the group now.")
            return

        # Check if this is a clarification response (Phase 2 escalation)
        if handle_clarification_response(message_data["chat_id"], message_data["text"]):
            await send_message(message_data["chat_id"], "Thanks — feeding your answer back into the pipeline.")
            return

        # Call the user-provided handler
        if handle_message_callback:
            await handle_message_callback(message_data)


async def _handle_callback_query_wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, handle_callback_callback):
    """
    Internal wrapper to handle inline keyboard button callbacks.

    Args:
        update: The Telegram update object
        context: The context object
        handle_callback_callback: The callback function to process the button press
    """
    if update.callback_query:
        query = update.callback_query
        await query.answer()  # Acknowledge the callback

        callback_data = {
            "callback_id": query.id,
            "chat_id": query.message.chat_id,
            "message_id": query.message.message_id,
            "data": query.data,
            "from_user": {
                "id": query.from_user.id,
                "username": query.from_user.username,
                "first_name": query.from_user.first_name,
            }
        }

        logger.info(f"Received callback from {callback_data['from_user']['username']}: {callback_data['data']}")

        # Call the user-provided handler
        if handle_callback_callback:
            await handle_callback_callback(callback_data)


def handle_gate2_response(chat_id: int, text: str) -> bool:
    """
    Check if this message is a Gate 2 response and process it.

    Returns True if handled as a Gate 2 response, False otherwise.
    """
    import json
    from memory.redis_client import redis_client

    text_lower = text.lower().strip()

    if not text_lower.startswith(("agree", "edit", "add", "kill")):
        return False

    group_key = f"gate2_active_group:{chat_id}"
    group_id = redis_client.get(group_key)
    if not group_id:
        return False

    if isinstance(group_id, bytes):
        group_id = group_id.decode("utf-8")

    pending_key = f"gate2_pending:{group_id}"
    if not redis_client.get(pending_key):
        return False

    if text_lower.startswith("agree"):
        response = {"action": "agree"}
    elif text_lower.startswith("edit"):
        response = {"action": "edit", "edits": {}, "raw": text[4:].strip()}
    elif text_lower.startswith("add"):
        response = {"action": "add", "new_task": text[3:].strip()}
    elif text_lower.startswith("kill"):
        response = {"action": "kill"}
    else:
        return False

    response_key = f"gate2_response:{group_id}"
    redis_client.set(response_key, json.dumps(response), ex=3600)
    redis_client.delete(pending_key)
    redis_client.delete(group_key)

    logger.info(f"[GATE2] Processed response from chat {chat_id}: {response['action']}")
    return True


def handle_clarification_response(chat_id: int, text: str) -> bool:
    """
    Check if there's a pending clarification for this chat and route Alex's reply back.

    The Mother Agent sets `awaiting_clarification:{chat_id}` when a child agent escalates.
    When Alex replies, we store the answer in Redis so the pipeline can resume.

    Returns True if handled, False otherwise.
    """
    import json
    from memory.redis_client import redis_client

    clarification_key = f"awaiting_clarification:{chat_id}"
    pending_raw = redis_client.get(clarification_key)
    if not pending_raw:
        return False

    if isinstance(pending_raw, bytes):
        pending_raw = pending_raw.decode("utf-8")

    try:
        pending = json.loads(pending_raw)
    except (json.JSONDecodeError, TypeError):
        return False

    task_id = pending.get("task_id")
    session_id = pending.get("session_id")
    run_id = pending.get("run_id")
    section = pending.get("section")

    text_lower = text.lower().strip()
    if text_lower == "agent":
        response = {"type": "delegate_to_agent", "section": section}
    else:
        response = {
            "type": "ceo_answer",
            "answer": text.strip(),
            "task_id": task_id,
            "session_id": session_id,
            "run_id": run_id,
            "section": section,
        }

    redis_client.set(f"clarification_response:{task_id}", json.dumps(response), ex=3600)
    redis_client.delete(clarification_key)

    logger.info("[CLARIFICATION] Processed response from chat %s for task %s", chat_id, task_id)
    return True


def set_gate2_active_group(chat_id: int, group_id: str):
    """Store the active gate2 group_id for a chat so responses can be routed."""
    from memory.redis_client import redis_client
    redis_client.set(f"gate2_active_group:{chat_id}", group_id, ex=14400)


def start_polling(handle_message, handle_callback=None):
    """
    Start polling Telegram for new messages and button callbacks.

    Args:
        handle_message: A callback function that will be called for each new message.
                       Should accept a dict with: message_id, chat_id, text, from_user
        handle_callback: Optional callback function for inline keyboard button presses.
                        Should accept a dict with: callback_id, chat_id, message_id, data, from_user
    """
    logger.info("Starting Telegram bot polling...")

    # Create application
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Add message handler
    async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await _handle_message_wrapper(update, context, handle_message)

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    # Add callback query handler for inline buttons
    if handle_callback:
        async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
            await _handle_callback_query_wrapper(update, context, handle_callback)

        application.add_handler(CallbackQueryHandler(callback_handler))
        logger.info("Callback query handler registered")

    # Start polling
    logger.info("Bot is now polling for messages...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)
