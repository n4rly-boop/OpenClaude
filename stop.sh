#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="claude-telegram-bot"
PIDFILE="$SCRIPT_DIR/.bot.pid"

if command -v systemctl &>/dev/null && systemctl --user is-active "$SERVICE_NAME" &>/dev/null 2>&1; then
    systemctl --user stop "$SERVICE_NAME"
    echo "Bot stopped (systemd)"
else
    # Fallback: kill by PID file
    if [ -f "$PIDFILE" ]; then
        PID=$(cat "$PIDFILE")
        if kill -0 "$PID" 2>/dev/null; then
            kill "$PID"
            echo "Stopped bot (PID $PID)"
        else
            echo "Bot was not running (stale PID)"
        fi
        rm -f "$PIDFILE"
    else
        echo "No PID file found"
    fi

    # Kill any orphaned processes
    ORPHANS=$(pgrep -f "python3.*telegram-bot.py" 2>/dev/null || true)
    if [ -n "$ORPHANS" ]; then
        echo "Killing orphaned processes: $ORPHANS"
        kill $ORPHANS 2>/dev/null || true
    fi

    echo "Bot stopped"
fi
