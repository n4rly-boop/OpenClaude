# Skill: telegram-sender

## Purpose
Send messages and files to Telegram chats directly via the Telegram Bot API.
Useful for proactive notifications, delivering files, and sending messages outside of the normal request-response flow.

## Usage

### Send a text message
```bash
./skills/telegram-sender/send.sh --text "Hello from Claude!" --chat CHAT_ID
```

### Send a file
```bash
./skills/telegram-sender/send.sh --file /path/to/file.pdf --chat CHAT_ID
```

### Send a file with caption
```bash
./skills/telegram-sender/send.sh --file /path/to/file.pdf --caption "Here's the report" --chat CHAT_ID
```

### Send with HTML formatting
```bash
./skills/telegram-sender/send.sh --text "<b>Important:</b> Task complete" --chat CHAT_ID --html
```

## Environment Variables
- `TELEGRAM_BOT_TOKEN` — Required. Read from .env if not set.
- `TELEGRAM_CHAT_ID` — Default chat ID. Can be overridden with --chat flag.

## Exit Codes
- 0: Success
- 1: Missing required parameters
- 2: API request failed
