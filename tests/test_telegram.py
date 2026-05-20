"""
Test suite for Telegram handler.
"""

import os
import asyncio
from pathlib import Path
from dotenv import load_dotenv
import sys

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.telegram_handler import send_message

# Load environment variables
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(env_path)


def test_telegram_send_message():
    """
    Test sending a message to Telegram.
    """
    # Get test chat ID from environment
    test_chat_id = os.getenv("TELEGRAM_TEST_CHAT_ID")

    if not test_chat_id:
        print("ERROR: TELEGRAM_TEST_CHAT_ID not set in .env file")
        return False

    try:
        test_chat_id = int(test_chat_id)
    except ValueError:
        print(f"ERROR: TELEGRAM_TEST_CHAT_ID must be a valid integer, got: {test_chat_id}")
        return False

    # Send test message
    test_message = "🤖 Test message from multi-agent AI system - Telegram handler is working!"

    print(f"\nSending test message to chat_id: {test_chat_id}")

    # Run the async function
    result = asyncio.run(send_message(test_chat_id, test_message))

    # Verify message was sent
    if not result:
        print("ERROR: Failed to send message to Telegram")
        return False

    print(f"✓ Message sent successfully to chat_id: {test_chat_id}")
    return True


if __name__ == "__main__":
    success = test_telegram_send_message()
    if not success:
        exit(1)
