"""
Test script to send messages to Telegram bot and verify the 3-question flow.
"""

import os
import asyncio
from pathlib import Path
from dotenv import load_dotenv
from telegram import Bot

# Load environment variables
load_dotenv(Path(__file__).parent / ".env")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_TEST_CHAT_ID = int(os.getenv("TELEGRAM_TEST_CHAT_ID"))

async def send_test_message(message_text: str):
    """Send a test message to the bot"""
    bot = Bot(token=TELEGRAM_BOT_TOKEN)

    print(f"\n{'='*60}")
    print(f"Sending message to bot...")
    print(f"Message: {message_text}")
    print(f"{'='*60}\n")

    await bot.send_message(chat_id=TELEGRAM_TEST_CHAT_ID, text=message_text)
    print("✓ Message sent")

async def main():
    """Run the test flow"""
    print("\n" + "="*60)
    print("TEST: 3-Question Flow with Auto L3 Trigger")
    print("="*60)

    print("\nThis will test:")
    print("1. Send vague message")
    print("2. Answer 3 questions")
    print("3. Verify L3 runs automatically after question 3")
    print("4. Verify summary arrives in Telegram")

    input("\nPress Enter to start the test...")

    # Step 1: Send initial vague message
    print("\n[STEP 1] Sending vague message...")
    await send_test_message("I want to grow my business")

    input("\nCheck Telegram for question 1/3. Answer it, then press Enter...")

    input("\nCheck Telegram for question 2/3. Answer it, then press Enter...")

    input("\nCheck Telegram for question 3/3. Answer it, then press Enter...")

    print("\n[VERIFICATION] After answering question 3, you should receive:")
    print("  - L3 feedback summary")
    print("  - WHAT WE KNOW section")
    print("  - BIGGEST OPEN RISK section")
    print("  - DECISION QUESTION with Yes/Adjust/Kill options")

    input("\nDid you receive the L3 summary? (Press Enter to continue)")

    print("\n" + "="*60)
    print("Test flow complete!")
    print("="*60)

if __name__ == "__main__":
    asyncio.run(main())
