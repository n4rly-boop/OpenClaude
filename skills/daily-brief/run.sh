#!/usr/bin/env bash
# daily-brief — Generate and send a morning briefing via Telegram
# Reads memory files, summarizes recent activity, and delivers via telegram-sender.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Source .env
if [[ -f "$PROJECT_DIR/.env" ]]; then
    set -a
    source "$PROJECT_DIR/.env"
    set +a
fi

echo "[daily-brief] Starting daily brief at $(date -u +%Y-%m-%dT%H:%M:%SZ)"

# Run Claude with daily brief prompt
cd "$PROJECT_DIR"

claude -p "Generate a morning briefing. Read memory files to recall context. Summarize: what happened yesterday, what's planned today, any pending items. Send the brief via telegram-sender skill." \
    --allowedTools "Read,Write,Edit,Bash,Glob,Grep,WebFetch,WebSearch,Skill"

echo "[daily-brief] Completed at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
