#!/usr/bin/env bash
# Ouroboros — watchdog that ensures the telegram bot stays alive.
# If the bot service is stopped or dead, it restarts it after a short delay.
set -euo pipefail

SERVICE="claude-telegram-bot"
CHECK_INTERVAL="${OUROBOROS_INTERVAL:-30}"

# Resolve project root (parent of bin/)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CLEANUP_MARKER="$PROJECT_DIR/.last-log-cleanup"
CLEANUP_INTERVAL=3600  # 1 hour in seconds

echo "Ouroboros watching $SERVICE (every ${CHECK_INTERVAL}s)"

while true; do
    if ! systemctl --user is-active "$SERVICE" &>/dev/null; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') [ouroboros] $SERVICE is dead — reviving..."

        # Notify users who had active generations when the bot crashed
        "$SCRIPT_DIR/notify-interrupted.sh" "$PROJECT_DIR/.active-streams.json" \
            "Something went wrong — restarting..." 2>/dev/null || true

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
        "$SCRIPT_DIR/log-cleanup.sh"
        touch "$CLEANUP_MARKER"
        echo "$(date '+%Y-%m-%d %H:%M:%S') [ouroboros] Log cleanup complete"
    fi

    sleep "$CHECK_INTERVAL"
done
