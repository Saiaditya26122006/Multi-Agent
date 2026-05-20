"""
Simple script to send a test message to Telegram.
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

async def send_message():
    """Send test message"""
    bot = Bot(token=TELEGRAM_BOT_TOKEN)

    message = "I want to grow my business internationally"

    print(f"Sending: {message}")
    await bot.send_message(chat_id=TELEGRAM_TEST_CHAT_ID, text=message)
    print("✓ Message sent!")

if __name__ == "__main__":
    asyncio.run(send_message())
