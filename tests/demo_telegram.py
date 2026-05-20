"""
Demo script for Telegram handler.
Demonstrates sending a message and explains polling.
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


async def demo():
    """Run a demo of the Telegram handler."""
    test_chat_id = os.getenv("TELEGRAM_TEST_CHAT_ID")

    if not test_chat_id:
        print("ERROR: TELEGRAM_TEST_CHAT_ID not set in .env file")
        return

    test_chat_id = int(test_chat_id)

    print("=" * 60)
    print("Telegram Handler Demo")
    print("=" * 60)

    # Demo 1: Send a message
    print("\n1. Testing send_message()...")
    message = "✅ Telegram handler is fully operational!"
    result = await send_message(test_chat_id, message)

    if result:
        print(f"   ✓ Successfully sent message to chat {test_chat_id}")
    else:
        print(f"   ✗ Failed to send message")

    # Demo 2: Send multiple messages
    print("\n2. Testing multiple messages...")
    messages = [
        "📝 Message 1: System initialized",
        "🔧 Message 2: All components ready",
        "🚀 Message 3: Multi-agent system online"
    ]

    for i, msg in enumerate(messages, 1):
        result = await send_message(test_chat_id, msg)
        if result:
            print(f"   ✓ Message {i}/3 sent")
        await asyncio.sleep(0.5)  # Small delay between messages

    print("\n3. Polling functionality:")
    print("   To test receiving messages, run:")
    print("   python3 tests/test_telegram_polling.py")
    print("   Then send messages to your bot!")

    print("\n" + "=" * 60)
    print("Demo complete! Check your Telegram chat.")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(demo())
