#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PIDFILE="$SCRIPT_DIR/.bot.pid"
LOGFILE="$SCRIPT_DIR/bot.log"

# Check if already running
if [ -f "$PIDFILE" ]; then
    PID=$(cat "$PIDFILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "Bot is already running (PID $PID)"
        echo "Use ./stop.sh to stop it first"
        exit 1
    else
        rm -f "$PIDFILE"
    fi
fi

# Check prerequisites
if ! command -v python3 &>/dev/null; then
    echo "Error: python3 not found"
    exit 1
fi

if [ ! -f "$SCRIPT_DIR/.env" ]; then
    echo "Error: .env file not found. Copy .env.example and fill in your values."
    exit 1
fi

# Ensure claude is in PATH
export PATH="$HOME/.local/bin:$PATH"

if ! command -v claude &>/dev/null; then
    echo "Error: claude CLI not found. Install it first."
    exit 1
fi

# Kill any orphaned bot processes using our token
pkill -f "python3.*telegram-bot.py" 2>/dev/null || true
sleep 2

# Start the bot
cd "$SCRIPT_DIR"
nohup python3 -u telegram-bot.py >> "$LOGFILE" 2>&1 &
BOT_PID=$!
echo "$BOT_PID" > "$PIDFILE"

# Wait a moment and verify it started
sleep 3
if kill -0 "$BOT_PID" 2>/dev/null; then
    echo "Bot started (PID $BOT_PID)"
    echo "Logs: $LOGFILE"
    echo "Stop:  ./stop.sh"
    tail -5 "$LOGFILE"
else
    echo "Bot failed to start. Check logs:"
    tail -20 "$LOGFILE"
    rm -f "$PIDFILE"
    exit 1
fi
