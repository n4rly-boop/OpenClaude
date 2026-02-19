#!/usr/bin/env bash
# guard-write.sh — PreToolUse hook that blocks Write/Edit to protected files.
# Exit 0 = allow, Exit 2 = block.
set -euo pipefail

FILEPATH=$(echo "$CLAUDE_TOOL_INPUT" | jq -r '.file_path // empty' 2>/dev/null)
if [ -z "$FILEPATH" ]; then
    exit 0
fi

# Block writes to SSH config, keys, firewall, PAM, and system auth files
if echo "$FILEPATH" | grep -qiE "/etc/ssh|authorized_keys|known_hosts|/etc/pam\.|/etc/nsswitch|/etc/shadow|/etc/passwd|/etc/iptables|/etc/nftables|/etc/ufw|guard\.sh|guard-write\.sh"; then
    echo "BLOCKED: You are not allowed to modify this protected file: $FILEPATH" >&2
    exit 2
fi

exit 0
