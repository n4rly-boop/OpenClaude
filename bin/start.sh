#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BOT_SERVICE="claude-telegram-bot"
OURO_SERVICE="ouroboros"
BOT_SERVICE_FILE="$PROJECT_DIR/services/systemd/$BOT_SERVICE.service"
OURO_SERVICE_FILE="$PROJECT_DIR/services/systemd/$OURO_SERVICE.service"
SYSTEMD_DEST="$HOME/.config/systemd/user"
LOGFILE="$PROJECT_DIR/bot.log"

export PATH="$HOME/.local/bin:$PATH"

# ── Prerequisite checks ───────────────────────────────────────────────

if ! command -v python3 &>/dev/null; then
    echo "Error: python3 not found"
    exit 1
fi

if [ ! -f "$PROJECT_DIR/.env" ]; then
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
    mkdir -p "$SYSTEMD_DEST"
    CURRENT_USER="$(whoami)"

    # Install/update both service files with real paths
    for svc_file in "$BOT_SERVICE_FILE" "$OURO_SERVICE_FILE"; do
        svc_name="$(basename "$svc_file")"
        sed -e "s|/path/to/OpenClaude|$PROJECT_DIR|g" \
            -e "s|your-username-here|$CURRENT_USER|g" \
            "$svc_file" > "$SYSTEMD_DEST/$svc_name"
    done

    systemctl --user daemon-reload

    # Start the bot service first, then ouroboros to watch it
    systemctl --user enable "$BOT_SERVICE" 2>/dev/null || true
    systemctl --user enable "$OURO_SERVICE" 2>/dev/null || true
    systemctl --user restart "$BOT_SERVICE"
    systemctl --user restart "$OURO_SERVICE"

    sleep 2
    echo "=== Bot service ==="
    systemctl --user status "$BOT_SERVICE" --no-pager -l || true
    echo ""
    echo "=== Ouroboros watchdog ==="
    systemctl --user status "$OURO_SERVICE" --no-pager -l || true
    echo ""
    echo "Bot + ouroboros started via systemd."
    echo "Logs: journalctl --user -u $BOT_SERVICE -f"
    echo "Stop: ./bin/stop.sh"
else
    # Fallback: nohup (start bot + ouroboros as background processes)
    PIDFILE="$PROJECT_DIR/.bot.pid"
    OURO_PIDFILE="$PROJECT_DIR/.ouroboros.pid"

    # Stop existing processes
    for pf in "$PIDFILE" "$OURO_PIDFILE"; do
        if [ -f "$pf" ]; then
            PID=$(cat "$pf")
            if kill -0 "$PID" 2>/dev/null; then
                kill "$PID" 2>/dev/null || true
            fi
            rm -f "$pf"
        fi
    done
    pkill -f "python3.*telegram-bot.py" 2>/dev/null || true
    pkill -f "bash.*ouroboros.sh" 2>/dev/null || true
    sleep 1

    cd "$PROJECT_DIR"

    # Start bot
    nohup python3 -u telegram-bot.py >> "$LOGFILE" 2>&1 &
    BOT_PID=$!
    echo "$BOT_PID" > "$PIDFILE"

    # Start ouroboros
    nohup bash bin/ouroboros.sh >> "$LOGFILE" 2>&1 &
    OURO_PID=$!
    echo "$OURO_PID" > "$OURO_PIDFILE"

    sleep 3
    if kill -0 "$BOT_PID" 2>/dev/null; then
        echo "Bot started (PID $BOT_PID)"
        echo "Ouroboros started (PID $OURO_PID)"
        echo "Logs: $LOGFILE"
        echo "Stop: ./bin/stop.sh"
        tail -5 "$LOGFILE"
    else
        echo "Bot failed to start. Check logs:"
        tail -20 "$LOGFILE"
        rm -f "$PIDFILE" "$OURO_PIDFILE"
        exit 1
    fi
fi
