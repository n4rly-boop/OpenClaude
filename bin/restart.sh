#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BOT_SERVICE="claude-telegram-bot"

echo "Restarting bot..."

# Snapshot active streams before the bot's finally blocks can empty them
cp "$PROJECT_DIR/.active-streams.json" "$PROJECT_DIR/.restart-state.json" 2>/dev/null || true

# Notify users with active generations
"$SCRIPT_DIR/notify-interrupted.sh" "$PROJECT_DIR/.restart-state.json" \
    "Restarting — back in a moment..." 2>/dev/null || true

if command -v systemctl &>/dev/null && systemctl --user is-active "$BOT_SERVICE" &>/dev/null 2>&1; then
    systemctl --user restart "$BOT_SERVICE"
    echo "Bot restarted (systemd). Ouroboros still watching."
else
    # Fallback: full stop/start cycle
    "$SCRIPT_DIR/stop.sh"
    sleep 2
    "$SCRIPT_DIR/start.sh"
fi
