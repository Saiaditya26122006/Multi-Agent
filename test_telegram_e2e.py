"""
End-to-end test via Telegram.
Sends a test message and monitors the console logs for the expected flow.
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
    print(f"Sending to Telegram...")
    print(f"Message: {message_text}")
    print(f"{'='*60}\n")

    await bot.send_message(chat_id=TELEGRAM_TEST_CHAT_ID, text=message_text)
    print("✓ Message sent to Telegram bot")
    print("\nCheck main.py console for processing logs...")

async def main():
    """Send initial test message"""
    print("\n" + "="*60)
    print("END-TO-END TEST: Full 3-Question Flow")
    print("="*60)

    print("\n[INFO] This will test the complete flow:")
    print("  1. Send vague message")
    print("  2. Bot asks question 1/3")
    print("  3. You answer via Telegram")
    print("  4. Bot asks question 2/3")
    print("  5. You answer via Telegram")
    print("  6. Bot asks question 3/3")
    print("  7. You answer via Telegram")
    print("  8. Bot auto-triggers L3 and sends feedback")
    print("  9. You receive WHAT WE KNOW + BIGGEST RISK + DECISION")

    input("\nPress Enter to send the initial vague message...")

    # Send the initial vague message
    await send_test_message("I want to grow my business internationally")

    print("\n" + "="*60)
    print("MESSAGE SENT")
    print("="*60)
    print("\n✓ Go to Telegram and check for question 1/3")
    print("✓ Watch main.py console for [L1] Questions asked so far: 0/3")
    print("\nAnswer the questions in Telegram, then return here...")

    input("\nPress Enter after you've answered all 3 questions and received L3 feedback...")

    print("\n" + "="*60)
    print("TEST COMPLETE")
    print("="*60)
    print("\nVerify the following:")
    print("  [ ] Received exactly 3 questions (no 4th question)")
    print("  [ ] Console showed: [L1] Questions asked so far: 0/3, 1/3, 2/3, 3/3")
    print("  [ ] Console showed: [L1] ✓ Maximum questions reached (3/3)")
    print("  [ ] Console showed: [L1] ✓ Triggering L3 Feedback Agent...")
    print("  [ ] Console showed: [L3] Generating feedback...")
    print("  [ ] Received L3 feedback in Telegram with:")
    print("      - WHAT WE KNOW section")
    print("      - BIGGEST OPEN RISK section")
    print("      - DECISION QUESTION with Yes/Adjust/Kill")

    print("\n" + "="*60)

if __name__ == "__main__":
    asyncio.run(main())
