#!/usr/bin/env bash
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "$0")" && pwd)"
WORKSPACE="${OPENCLAUDE_WORKSPACE_DIR:?OPENCLAUDE_WORKSPACE_DIR must be set}"
CRON_STATE="$WORKSPACE/tg-client-cron.json"
LOG_FILE="$WORKSPACE/logs/tg-client-monitor.log"

usage() {
    cat <<EOF
Usage: $0 {start [--interval N] [--since-minutes M]|stop|status}
  start   Install cron job (default interval: 60 min, since-minutes: 12)
  stop    Remove cron job
  status  Show if cron is running + last log lines
EOF
    exit 1
}

ensure_log_dir() {
    mkdir -p "$(dirname "$LOG_FILE")"
}

cron_tag() {
    echo "tg-client-monitor-${WORKSPACE##*/}"
}

cmd_start() {
    local interval=60
    local since_minutes=12

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --interval) interval="$2"; shift 2 ;;
            --since-minutes) since_minutes="$2"; shift 2 ;;
            *) echo "Unknown arg: $1" >&2; exit 1 ;;
        esac
    done

    ensure_log_dir

    local tag
    tag="$(cron_tag)"

    # Remove existing cron for this workspace
    crontab -l 2>/dev/null | grep -v "$tag" | crontab - 2>/dev/null || true

    # Build cron command
    local cron_cmd="cd $SKILL_DIR && OPENCLAUDE_WORKSPACE_DIR=$WORKSPACE python3 tg.py monitor --since-minutes $since_minutes >> $LOG_FILE 2>&1 # $tag"

    # Install cron
    (crontab -l 2>/dev/null; echo "*/$interval * * * * $cron_cmd") | crontab -

    # Save state
    cat > "$CRON_STATE" <<EOFJSON
{
    "active": true,
    "interval_minutes": $interval,
    "since_minutes": $since_minutes,
    "installed_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
    "tag": "$tag"
}
EOFJSON

    echo "Cron installed: every $interval minutes, fetching last $since_minutes minutes"
    echo "Log: $LOG_FILE"
}

cmd_stop() {
    local tag
    tag="$(cron_tag)"

    crontab -l 2>/dev/null | grep -v "$tag" | crontab - 2>/dev/null || true

    if [[ -f "$CRON_STATE" ]]; then
        # Update state
        python3 -c "
import json, sys
with open('$CRON_STATE') as f:
    s = json.load(f)
s['active'] = False
with open('$CRON_STATE', 'w') as f:
    json.dump(s, f, indent=2)
" 2>/dev/null || true
    fi

    echo "Cron removed."
}

cmd_status() {
    local tag
    tag="$(cron_tag)"

    echo "=== Cron Status ==="
    if crontab -l 2>/dev/null | grep -q "$tag"; then
        echo "Status: ACTIVE"
        crontab -l 2>/dev/null | grep "$tag"
    else
        echo "Status: NOT ACTIVE"
    fi

    if [[ -f "$CRON_STATE" ]]; then
        echo ""
        echo "=== State ==="
        cat "$CRON_STATE"
    fi

    if [[ -f "$LOG_FILE" ]]; then
        echo ""
        echo "=== Last 20 log lines ==="
        tail -20 "$LOG_FILE"
    else
        echo ""
        echo "No log file found."
    fi
}

case "${1:-}" in
    start)  shift; cmd_start "$@" ;;
    stop)   cmd_stop ;;
    status) cmd_status ;;
    *)      usage ;;
esac
