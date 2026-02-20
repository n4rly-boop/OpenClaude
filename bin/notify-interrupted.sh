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

# File to store sent message IDs for later editing
RESTART_MESSAGES_FILE="$PROJECT_DIR/.restart-messages.json"

# Send notification to each chat and save message IDs
echo "[" > "$RESTART_MESSAGES_FILE"
FIRST_ENTRY=true

while read -r CHAT_ID THREAD_ID; do
    PAYLOAD="{\"chat_id\": $CHAT_ID, \"text\": \"$MESSAGE\"}"
    if [[ "$THREAD_ID" -ne 0 ]]; then
        PAYLOAD="{\"chat_id\": $CHAT_ID, \"text\": \"$MESSAGE\", \"message_thread_id\": $THREAD_ID}"
    fi
    RESPONSE=$(curl -s -X POST \
        "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -H "Content-Type: application/json" \
        -d "$PAYLOAD" 2>/dev/null) || true

    # Parse message_id from response and save for later editing
    MSG_ID=$(echo "$RESPONSE" | python3 -c "
import json, sys
try:
    r = json.load(sys.stdin)
    if r.get('ok'):
        print(r['result']['message_id'])
except Exception:
    pass
" 2>/dev/null) || true

    if [[ -n "$MSG_ID" ]]; then
        if [[ "$FIRST_ENTRY" != true ]]; then
            echo "," >> "$RESTART_MESSAGES_FILE"
        fi
        FIRST_ENTRY=false
        echo "{\"chat_id\": $CHAT_ID, \"thread_id\": $THREAD_ID, \"message_id\": $MSG_ID}" >> "$RESTART_MESSAGES_FILE"
    fi
done <<< "$CHATS"

echo "]" >> "$RESTART_MESSAGES_FILE"
