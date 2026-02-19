#!/usr/bin/env bash
# Ouroboros — watchdog that ensures the telegram bot stays alive.
# If the bot service is stopped or dead, it restarts it after a short delay.
set -euo pipefail

SERVICE="claude-telegram-bot"
CHECK_INTERVAL="${OUROBOROS_INTERVAL:-30}"

# Resolve project root (parent of bin/)
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLEANUP_MARKER="$PROJECT_DIR/.last-log-cleanup"
CLEANUP_INTERVAL=3600  # 1 hour in seconds

echo "Ouroboros watching $SERVICE (every ${CHECK_INTERVAL}s)"

while true; do
    if ! systemctl --user is-active "$SERVICE" &>/dev/null; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') [ouroboros] $SERVICE is dead — reviving..."
        systemctl --user start "$SERVICE"
        sleep 5
        if systemctl --user is-active "$SERVICE" &>/dev/null; then
            echo "$(date '+%Y-%m-%d %H:%M:%S') [ouroboros] $SERVICE revived successfully"
        else
            echo "$(date '+%Y-%m-%d %H:%M:%S') [ouroboros] $SERVICE failed to start!" >&2
        fi
    fi

    # Hourly log cleanup — gated by marker file mtime
    _do_cleanup=false
    if [[ ! -f "$CLEANUP_MARKER" ]]; then
        _do_cleanup=true
    else
        _marker_age=$(( $(date +%s) - $(stat -c %Y "$CLEANUP_MARKER" 2>/dev/null || echo 0) ))
        if (( _marker_age >= CLEANUP_INTERVAL )); then
            _do_cleanup=true
        fi
    fi

    if $_do_cleanup; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') [ouroboros] Running log cleanup..."
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
                echo "$(date '+%Y-%m-%d %H:%M:%S') [ouroboros] Truncated bot.log (was ${_size} bytes)"
            fi
        fi
        touch "$CLEANUP_MARKER"
        echo "$(date '+%Y-%m-%d %H:%M:%S') [ouroboros] Log cleanup complete"
    fi

    sleep "$CHECK_INTERVAL"
done
