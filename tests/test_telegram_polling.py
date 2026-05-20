"""
Test the Telegram polling functionality.
Run this to test receiving messages from Telegram.
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.telegram_handler import start_polling

# Load environment variables
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(env_path)


async def handle_message(message_data):
    """
    Handler function that processes incoming messages.

    Args:
        message_data: Dict with message_id, chat_id, text, from_user
    """
    print(f"\n{'='*60}")
    print(f"Processing message:")
    print(f"  Message ID: {message_data['message_id']}")
    print(f"  Chat ID: {message_data['chat_id']}")
    print(f"  From: {message_data['from_user']['username']} ({message_data['from_user']['first_name']})")
    print(f"  Text: {message_data['text']}")
    print(f"{'='*60}\n")

    # Here you would normally process the message through your AI agents
    # For testing, we just log it


if __name__ == "__main__":
    print("Starting Telegram polling test...")
    print("Send a message to your bot to test!")
    print("Press Ctrl+C to stop\n")

    try:
        start_polling(handle_message)
    except KeyboardInterrupt:
        print("\n\nStopping bot...")
