#!/usr/bin/env bash
# Ouroboros — watchdog that ensures the telegram bot stays alive.
# If the bot service is stopped or dead, it restarts it after a short delay.
set -euo pipefail

SERVICE="claude-telegram-bot"
CHECK_INTERVAL="${OUROBOROS_INTERVAL:-30}"

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
    sleep "$CHECK_INTERVAL"
done
