#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BOT_SERVICE="claude-telegram-bot"
OURO_SERVICE="ouroboros"
PIDFILE="$PROJECT_DIR/.bot.pid"
OURO_PIDFILE="$PROJECT_DIR/.ouroboros.pid"

# ── systemd path ──────────────────────────────────────────────────────

_stopped_systemd=0

if command -v systemctl &>/dev/null; then
    # Stop ouroboros first so it doesn't revive the bot
    if systemctl --user is-active "$OURO_SERVICE" &>/dev/null 2>&1; then
        systemctl --user stop "$OURO_SERVICE"
        echo "Ouroboros stopped (systemd)"
        _stopped_systemd=1
    fi
    if systemctl --user is-active "$BOT_SERVICE" &>/dev/null 2>&1; then
        systemctl --user stop "$BOT_SERVICE"
        echo "Bot stopped (systemd)"
        _stopped_systemd=1
    fi
fi

if [[ "$_stopped_systemd" -eq 1 ]]; then
    echo "All services stopped."
    exit 0
fi

# ── Fallback: kill by PID files ───────────────────────────────────────

# Stop ouroboros first so it doesn't revive the bot
for label_pf in "Ouroboros:$OURO_PIDFILE" "Bot:$PIDFILE"; do
    label="${label_pf%%:*}"
    pf="${label_pf##*:}"
    if [ -f "$pf" ]; then
        PID=$(cat "$pf")
        if kill -0 "$PID" 2>/dev/null; then
            kill "$PID"
            echo "$label stopped (PID $PID)"
        else
            echo "$label was not running (stale PID)"
        fi
        rm -f "$pf"
    fi
done

# Kill any orphaned processes
ORPHANS=$(pgrep -f "bash.*ouroboros.sh" 2>/dev/null || true)
if [ -n "$ORPHANS" ]; then
    echo "Killing orphaned ouroboros: $ORPHANS"
    kill $ORPHANS 2>/dev/null || true
fi

ORPHANS=$(pgrep -f "python3.*telegram-bot.py" 2>/dev/null || true)
if [ -n "$ORPHANS" ]; then
    echo "Killing orphaned bot: $ORPHANS"
    kill $ORPHANS 2>/dev/null || true
fi

echo "All processes stopped."
