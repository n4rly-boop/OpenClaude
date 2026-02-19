#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="claude-telegram-bot"
SERVICE_FILE="$SCRIPT_DIR/systemd/$SERVICE_NAME.service"
SYSTEMD_DEST="$HOME/.config/systemd/user"
LOGFILE="$SCRIPT_DIR/bot.log"

export PATH="$HOME/.local/bin:$PATH"

# ── Prerequisite checks ───────────────────────────────────────────────

if ! command -v python3 &>/dev/null; then
    echo "Error: python3 not found"
    exit 1
fi

if [ ! -f "$SCRIPT_DIR/.env" ]; then
    echo "Error: .env not found. Run setup.sh first."
    exit 1
fi

if ! command -v claude &>/dev/null; then
    echo "Error: claude CLI not found. Install it first."
    exit 1
fi

# ── systemd path ──────────────────────────────────────────────────────

if command -v systemctl &>/dev/null && systemctl --user status &>/dev/null 2>&1; then
    USE_SYSTEMD=1
else
    USE_SYSTEMD=0
fi

if [[ "$USE_SYSTEMD" -eq 1 ]]; then
    # Install/update service file with real paths
    mkdir -p "$SYSTEMD_DEST"
    CURRENT_USER="$(whoami)"
    sed -e "s|/path/to/OpenClaude|$SCRIPT_DIR|g" \
        -e "s|your-username-here|$CURRENT_USER|g" \
        "$SERVICE_FILE" > "$SYSTEMD_DEST/$SERVICE_NAME.service"

    systemctl --user daemon-reload
    systemctl --user enable "$SERVICE_NAME" 2>/dev/null || true
    systemctl --user restart "$SERVICE_NAME"

    sleep 2
    systemctl --user status "$SERVICE_NAME" --no-pager -l || true
    echo ""
    echo "Bot started via systemd. Logs: journalctl --user -u $SERVICE_NAME -f"
    echo "Stop: ./stop.sh"
else
    # Fallback: nohup
    PIDFILE="$SCRIPT_DIR/.bot.pid"

    if [ -f "$PIDFILE" ]; then
        PID=$(cat "$PIDFILE")
        if kill -0 "$PID" 2>/dev/null; then
            echo "Bot is already running (PID $PID). Use ./stop.sh first."
            exit 1
        else
            rm -f "$PIDFILE"
        fi
    fi

    pkill -f "python3.*telegram-bot.py" 2>/dev/null || true
    sleep 1

    cd "$SCRIPT_DIR"
    nohup python3 -u telegram-bot.py >> "$LOGFILE" 2>&1 &
    BOT_PID=$!
    echo "$BOT_PID" > "$PIDFILE"

    sleep 3
    if kill -0 "$BOT_PID" 2>/dev/null; then
        echo "Bot started (PID $BOT_PID)"
        echo "Logs: $LOGFILE"
        echo "Stop: ./stop.sh"
        tail -5 "$LOGFILE"
    else
        echo "Bot failed to start. Check logs:"
        tail -20 "$LOGFILE"
        rm -f "$PIDFILE"
        exit 1
    fi
fi
