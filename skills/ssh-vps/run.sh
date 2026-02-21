#!/usr/bin/env bash
# ssh-vps — Run a command on the VPS via sshpass
# Usage: ./skills/ssh-vps/run.sh "command"
# If no command given, prints connection info.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Source .env — project first, then cwd (workspace overrides)
for env_file in "$PROJECT_DIR/.env" "$PWD/.env"; do
    if [[ -f "$env_file" ]]; then
        set -a
        source "$env_file"
        set +a
    fi
done

# Validate required env vars
: "${VPS_HOST:?VPS_HOST not set in .env}"
: "${VPS_PORT:?VPS_PORT not set in .env}"
: "${VPS_USER:?VPS_USER not set in .env}"
: "${VPS_PASSWORD:?VPS_PASSWORD not set in .env}"

REMOTE_CMD="${1:-}"

if [[ -z "$REMOTE_CMD" ]]; then
    echo "VPS: ${VPS_USER}@${VPS_HOST}:${VPS_PORT}"
    echo "Usage: $0 \"command to run\""
    exit 0
fi

sshpass -p "$VPS_PASSWORD" ssh \
    -o StrictHostKeyChecking=no \
    -o ConnectTimeout=15 \
    -p "$VPS_PORT" \
    "${VPS_USER}@${VPS_HOST}" \
    "$REMOTE_CMD"
