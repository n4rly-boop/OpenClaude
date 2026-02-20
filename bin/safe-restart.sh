#!/usr/bin/env bash
# safe-restart.sh — Test-gated restart with rollback and admin notification.
# Called by ouroboros when the bot is dead. Runs tests before starting,
# rolls back to the last known-good commit if tests or startup fail.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SERVICE="claude-telegram-bot"
KNOWN_GOOD_FILE="$PROJECT_DIR/backups/known-good-commit"
MAX_RETRIES=3
STARTUP_WAIT=15

# Load .env for TELEGRAM_CHAT_ID / ALLOWED_USERS
if [[ -f "$PROJECT_DIR/.env" ]]; then
    set -a
    source "$PROJECT_DIR/.env"
    set +a
fi

# Resolve admin chat ID: TELEGRAM_CHAT_ID, or first entry in ALLOWED_USERS
ADMIN_CHAT_ID="${TELEGRAM_CHAT_ID:-}"
if [[ -z "$ADMIN_CHAT_ID" && -n "${ALLOWED_USERS:-}" ]]; then
    ADMIN_CHAT_ID="${ALLOWED_USERS%%,*}"
    ADMIN_CHAT_ID="${ADMIN_CHAT_ID// /}"
fi

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') [safe-restart] $*"
}

notify() {
    local msg="$1"
    log "NOTIFY: $msg"
    if [[ -n "$ADMIN_CHAT_ID" ]]; then
        "$SCRIPT_DIR/../skills/telegram-sender/send.sh" \
            --text "$msg" --chat "$ADMIN_CHAT_ID" 2>/dev/null || true
    fi
}

get_short_hash() {
    git -C "$PROJECT_DIR" rev-parse --short HEAD 2>/dev/null || echo "unknown"
}

sync_main() {
    log "Syncing with origin/main..."
    cd "$PROJECT_DIR"

    if ! git fetch origin 2>/dev/null; then
        log "git fetch failed (offline?), continuing with local state"
        return 0
    fi

    # Only merge if there's something to merge
    local local_head remote_head
    local_head=$(git rev-parse HEAD 2>/dev/null)
    remote_head=$(git rev-parse origin/main 2>/dev/null || echo "")

    if [[ -z "$remote_head" || "$local_head" == "$remote_head" ]]; then
        log "Already up to date with origin/main"
        return 0
    fi

    if ! git merge origin/main --no-edit 2>/dev/null; then
        git merge --abort 2>/dev/null || true
        notify "[safe-restart] Merge conflict with origin/main on commit $(get_short_hash). Manual resolution needed."
        return 1
    fi

    log "Merged origin/main successfully"
    return 0
}

run_tests() {
    log "Running tests..."
    local output
    if output=$(cd "$PROJECT_DIR" && python3 -m pytest tests/ --tb=short -q -x 2>&1); then
        log "Tests passed"
        return 0
    else
        log "Tests FAILED"
        # Store last 20 lines for notification
        TEST_OUTPUT=$(echo "$output" | tail -20)
        return 1
    fi
}

record_good() {
    mkdir -p "$PROJECT_DIR/backups"
    git -C "$PROJECT_DIR" rev-parse HEAD > "$KNOWN_GOOD_FILE"
    log "Recorded known-good commit: $(get_short_hash)"
}

rollback() {
    if [[ ! -f "$KNOWN_GOOD_FILE" ]]; then
        notify "[safe-restart] No known-good commit found at $KNOWN_GOOD_FILE. Manual intervention needed."
        return 1
    fi

    local good_commit
    good_commit=$(cat "$KNOWN_GOOD_FILE")
    local current_commit
    current_commit=$(git -C "$PROJECT_DIR" rev-parse HEAD 2>/dev/null || echo "unknown")

    if [[ "$current_commit" == "$good_commit" ]]; then
        notify "[safe-restart] Already at known-good commit ($good_commit). Known-good is also failing. Manual intervention needed."
        return 1
    fi

    log "Rolling back from $(get_short_hash) to ${good_commit:0:7}..."
    git -C "$PROJECT_DIR" reset --hard "$good_commit"
    return 0
}

start_service() {
    log "Starting $SERVICE..."
    systemctl --user start "$SERVICE"
    sleep "$STARTUP_WAIT"
    if systemctl --user is-active "$SERVICE" &>/dev/null; then
        log "$SERVICE started successfully"
        return 0
    else
        log "$SERVICE failed to start after ${STARTUP_WAIT}s"
        return 1
    fi
}

# ── Main flow ────────────────────────────────────────────────────────

TEST_OUTPUT=""

# Step 1: Sync with main
if ! sync_main; then
    exit 1
fi

# Step 2: Test + start loop with retries
for attempt in $(seq 1 "$MAX_RETRIES"); do
    log "Attempt $attempt/$MAX_RETRIES"

    if run_tests; then
        if start_service; then
            record_good
            log "Bot is alive and healthy"
            exit 0
        else
            # Startup failed despite tests passing
            notify "[safe-restart] Tests passed but $SERVICE failed to start (attempt $attempt/$MAX_RETRIES). Commit: $(get_short_hash).
Last output:
$TEST_OUTPUT"
        fi
    else
        notify "[safe-restart] Tests FAILED (attempt $attempt/$MAX_RETRIES). Commit: $(get_short_hash).
Test output:
$TEST_OUTPUT"
    fi

    # Roll back and retry
    if ! rollback; then
        # rollback already sent notification
        exit 1
    fi
done

notify "[safe-restart] All $MAX_RETRIES attempts exhausted. Manual intervention needed."
exit 1
