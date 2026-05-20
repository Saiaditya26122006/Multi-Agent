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
