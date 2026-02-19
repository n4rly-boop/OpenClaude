#!/usr/bin/env bash
# Notify users with active generations that the bot is restarting/recovering.
# Usage: notify-interrupted.sh <streams-file> <message>
# Reads .env for TELEGRAM_BOT_TOKEN, parses JSON with python3, sends via curl.
set -euo pipefail

STREAMS_FILE="${1:-}"
MESSAGE="${2:-}"

if [[ -z "$STREAMS_FILE" || -z "$MESSAGE" || ! -f "$STREAMS_FILE" ]]; then
    exit 0
fi

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Load bot token from .env
TELEGRAM_BOT_TOKEN=""
if [[ -f "$PROJECT_DIR/.env" ]]; then
    TELEGRAM_BOT_TOKEN=$(grep -E '^TELEGRAM_BOT_TOKEN=' "$PROJECT_DIR/.env" | cut -d= -f2- | tr -d '"' | tr -d "'")
fi

if [[ -z "$TELEGRAM_BOT_TOKEN" ]]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') [notify] No TELEGRAM_BOT_TOKEN found" >&2
    exit 0
fi

# Parse JSON and extract unique chat_id + thread_id pairs
CHATS=$(python3 -c "
import json, sys
try:
    data = json.load(open('$STREAMS_FILE'))
    seen = set()
    for v in data.values():
        key = (v['chat_id'], v.get('thread_id', 0))
        if key not in seen:
            seen.add(key)
            print(f\"{v['chat_id']} {v.get('thread_id', 0)}\")
except Exception:
    sys.exit(0)
" 2>/dev/null) || exit 0

if [[ -z "$CHATS" ]]; then
    exit 0
fi

# Send notification to each chat
while read -r CHAT_ID THREAD_ID; do
    PAYLOAD="{\"chat_id\": $CHAT_ID, \"text\": \"$MESSAGE\"}"
    if [[ "$THREAD_ID" -ne 0 ]]; then
        PAYLOAD="{\"chat_id\": $CHAT_ID, \"text\": \"$MESSAGE\", \"message_thread_id\": $THREAD_ID}"
    fi
    curl -s -X POST \
        "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -H "Content-Type: application/json" \
        -d "$PAYLOAD" >/dev/null 2>&1 || true
done <<< "$CHATS"
