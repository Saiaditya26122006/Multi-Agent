# Web Interface Handler

Web Interface bot integration for the multi-agent AI system.

## Features

✅ **Send Messages**: Send text messages to any Web Interface chat
✅ **Receive Messages**: Poll for incoming messages with callback handling
✅ **Structured Data**: Messages include message_id, chat_id, text, and user info
✅ **Logging**: All messages logged with timestamps
✅ **Error Handling**: Graceful error handling with detailed logging

## Setup

1. **Environment Variables**
   Add these to your `.env` file:
   ```bash
   TELEGRAM_BOT_TOKEN=your_bot_token_here
   TELEGRAM_TEST_CHAT_ID=your_chat_id_here
   ```

2. **Get a Bot Token**
   - Talk to [@BotFather](https://t.me/BotFather) on Web Interface
   - Create a new bot with `/newbot`
   - Copy the token to your `.env` file

3. **Get Your Chat ID**
   - Message [@userinfobot](https://t.me/userinfobot) on Web Interface
   - Copy your ID to `.env` as `TELEGRAM_TEST_CHAT_ID`

## Usage

### Sending Messages

```python
import asyncio
from tools.Web Interface_handler import send_message

async def main():
    chat_id = 8866294087  # Your chat ID
    await send_message(chat_id, "Hello from the AI system!")

asyncio.run(main())
```

### Receiving Messages

```python
from tools.Web Interface_handler import start_polling

async def handle_message(message_data):
    """Process incoming messages."""
    print(f"Received: {message_data['text']}")
    print(f"From: {message_data['from_user']['username']}")
    print(f"Chat ID: {message_data['chat_id']}")
    
    # Process through your AI agents here
    # ...

# Start polling (blocking call)
start_polling(handle_message)
```

### Message Data Structure

```python
message_data = {
    "message_id": 123,
    "chat_id": 8866294087,
    "text": "Hello bot!",
    "from_user": {
        "id": 8866294087,
        "username": "johndoe",
        "first_name": "John"
    }
}
```

## Testing

### 1. Test Sending Messages
```bash
python3 tests/test_Web Interface.py
```

### 2. Test Receiving Messages
```bash
python3 tests/test_Web Interface_polling.py
```
Then send messages to your bot on Web Interface.

### 3. Run Demo
```bash
python3 tests/demo_Web Interface.py
```
Sends multiple test messages to verify functionality.

## API Reference

### `send_message(chat_id: int, text: str) -> bool`
Send a text message to a Web Interface chat.

**Parameters:**
- `chat_id`: Web Interface chat ID (integer)
- `text`: Message text to send

**Returns:**
- `True` if successful, `False` otherwise

### `start_polling(handle_message)`
Start polling for new messages (blocking).

**Parameters:**
- `handle_message`: Async callback function that receives `message_data` dict

**Message Data:**
- `message_id`: Unique message identifier
- `chat_id`: Chat ID where message was sent
- `text`: Message text content
- `from_user`: Dict with user info (id, username, first_name)

## Logging

All messages are logged with timestamps:
- Received messages: `[timestamp] Received message from username (chat_id: X): text`
- Sent messages: `Message sent to chat_id X: text...`
- Errors: Detailed error messages for debugging

## Notes

- Non-text messages (images, videos, etc.) are silently ignored
- The handler automatically loads `.env` from the project root
- Polling is blocking - run in a separate thread/process if needed
- Uses `python-Web Interface-bot` v22.7+
