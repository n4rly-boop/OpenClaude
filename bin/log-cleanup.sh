#!/usr/bin/env bash
# Log cleanup — delete old rotated logs and truncate oversized bot.log.
# Called by ouroboros on an hourly cadence.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Delete rotated workspace logs older than 3 days
find "$PROJECT_DIR/workspaces/" -name "*.log.*" -mtime +3 -delete 2>/dev/null || true

# Delete rotated infra logs older than 7 days
find "$PROJECT_DIR/logs/" -name "*.log.*" -mtime +7 -delete 2>/dev/null || true

# Truncate bot.log to last 1000 lines if >10 MB
_botlog="$PROJECT_DIR/bot.log"
if [[ -f "$_botlog" ]]; then
    _size=$(stat -c %s "$_botlog" 2>/dev/null || echo 0)
    if (( _size > 10485760 )); then
        tail -n 1000 "$_botlog" > "$_botlog.tmp" && mv "$_botlog.tmp" "$_botlog"
        echo "$(date '+%Y-%m-%d %H:%M:%S') [log-cleanup] Truncated bot.log (was ${_size} bytes)"
    fi
fi
