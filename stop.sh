#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PIDFILE="$SCRIPT_DIR/.bot.pid"

# Kill by PID file
if [ -f "$PIDFILE" ]; then
    PID=$(cat "$PIDFILE")
    if kill -0 "$PID" 2>/dev/null; then
        kill "$PID"
        echo "Stopped bot (PID $PID)"
    else
        echo "Bot was not running (stale PID $PID)"
    fi
    rm -f "$PIDFILE"
else
    echo "No PID file found"
fi

# Also kill any orphaned processes
ORPHANS=$(pgrep -f "python3.*telegram-bot.py" 2>/dev/null || true)
if [ -n "$ORPHANS" ]; then
    echo "Killing orphaned processes: $ORPHANS"
    kill $ORPHANS 2>/dev/null || true
fi

echo "Bot stopped"
