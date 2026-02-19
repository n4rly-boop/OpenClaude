#!/usr/bin/env bash
# heartbeat — Periodic proactive check-in
# Reviews pending tasks, checks memory for reminders, sends updates if notable.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Source .env
if [[ -f "$PROJECT_DIR/.env" ]]; then
    set -a
    source "$PROJECT_DIR/.env"
    set +a
fi

STATE_FILE="$PROJECT_DIR/heartbeat-state.json"

# Initialize state file if it doesn't exist
if [[ ! -f "$STATE_FILE" ]]; then
    printf '{"last_run": null, "last_message_sent": null}\n' > "$STATE_FILE"
fi

# Read last run timestamp
LAST_RUN=$(python3 -c "
import json, sys
try:
    with open('$STATE_FILE') as f:
        data = json.load(f)
    print(data.get('last_run') or 'never')
except Exception:
    print('never')
")

echo "[heartbeat] Last run: $LAST_RUN"
echo "[heartbeat] Starting heartbeat at $(date -u +%Y-%m-%dT%H:%M:%SZ)"

# Run Claude with heartbeat prompt
cd "$PROJECT_DIR"

claude -p "Run heartbeat: review pending tasks, check memory for reminders or things you wanted to follow up on. Last heartbeat ran at: $LAST_RUN. If anything notable, send a brief update via the telegram-sender skill. If nothing to report, just say 'Heartbeat: nothing to report' and do not send a Telegram message." \
    --allowedTools "Read,Write,Edit,Bash,Glob,Grep,WebFetch,WebSearch,Skill"

# Update state file with new timestamp
NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)
python3 -c "
import json
try:
    with open('$STATE_FILE') as f:
        data = json.load(f)
except Exception:
    data = {}
data['last_run'] = '$NOW'
with open('$STATE_FILE', 'w') as f:
    json.dump(data, f, indent=2)
    f.write('\n')
"

echo "[heartbeat] Completed at $NOW"
