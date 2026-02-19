#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Restarting bot..."

"$SCRIPT_DIR/stop.sh"
sleep 2
"$SCRIPT_DIR/start.sh"
