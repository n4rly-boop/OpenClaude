#!/usr/bin/env bash
# ai-news — Fetch and send daily AI news digest via Telegram

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Source .env
if [[ -f "$PROJECT_DIR/.env" ]]; then
    set -a
    source "$PROJECT_DIR/.env"
    set +a
fi

echo "[ai-news] Starting AI news digest at $(date -u +%Y-%m-%dT%H:%M:%SZ)"

cd "$PROJECT_DIR"

claude -p "Search the web for today's top AI news (use WebSearch with queries like 'AI news today', 'artificial intelligence latest 2026'). Pick 5-7 most interesting stories. Format a concise digest in Russian with bullet points — for each item: bold title, 1-2 sentence summary, source name. Header: '🤖 AI-дайджест — [today's date]'. Send the message via the telegram-sender skill." \
    --allowedTools "Read,Bash,Glob,Grep,WebFetch,WebSearch,Skill"

echo "[ai-news] Completed at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
