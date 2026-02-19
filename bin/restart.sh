#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BOT_SERVICE="claude-telegram-bot"

echo "Restarting bot..."

if command -v systemctl &>/dev/null && systemctl --user is-active "$BOT_SERVICE" &>/dev/null 2>&1; then
    systemctl --user restart "$BOT_SERVICE"
    echo "Bot restarted (systemd). Ouroboros still watching."
else
    # Fallback: full stop/start cycle
    "$SCRIPT_DIR/stop.sh"
    sleep 2
    "$SCRIPT_DIR/start.sh"
fi
