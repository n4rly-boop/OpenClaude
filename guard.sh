#!/usr/bin/env bash
# guard.sh — PreToolUse hook that blocks dangerous Bash commands.
# Called by .claude/settings.json before every Bash tool invocation.
# Exit 0 = allow, Exit 2 = block.
set -euo pipefail

CMD=$(echo "$CLAUDE_TOOL_INPUT" | jq -r '.command // empty' 2>/dev/null)
if [ -z "$CMD" ]; then
    exit 0
fi

# Also check Write/Edit tool for protected file paths
FILEPATH=$(echo "$CLAUDE_TOOL_INPUT" | jq -r '.file_path // empty' 2>/dev/null)

# ── Blocked patterns ──────────────────────────────────────────────────

# 1. Service management (bot, ouroboros, or any service stop/restart)
if echo "$CMD" | grep -qiE "systemctl|service\s+(stop|restart|start)|kill\s|pkill\s|killall\s|claude-telegram-bot|ouroboros"; then
    echo "BLOCKED: You are not allowed to manage system services. Use ./restart.sh for the bot." >&2
    exit 2
fi

# 2. SSH access — do not touch sshd, its config, keys, or firewall rules for port 22
if echo "$CMD" | grep -qiE "sshd|ssh_config|authorized_keys|/etc/ssh"; then
    echo "BLOCKED: You are not allowed to modify SSH configuration or keys." >&2
    exit 2
fi

# 3. Firewall — block iptables/nftables/ufw changes that could lock out SSH
if echo "$CMD" | grep -qiE "\b(iptables|ip6tables|nftables|nft|ufw)\b"; then
    echo "BLOCKED: You are not allowed to modify firewall rules." >&2
    exit 2
fi

# 4. Network interfaces — don't bring down networking
if echo "$CMD" | grep -qiE "\b(ifconfig|ip\s+(link|addr|route))\b.*\b(down|del|flush)\b|nmcli.*down|networkctl.*down"; then
    echo "BLOCKED: You are not allowed to disable network interfaces." >&2
    exit 2
fi

# 5. PAM / NSS — don't mess with authentication modules
if echo "$CMD" | grep -qiE "/etc/pam\.|/etc/nsswitch"; then
    echo "BLOCKED: You are not allowed to modify PAM or NSS configuration." >&2
    exit 2
fi

# 6. User/password management that could lock out the admin
if echo "$CMD" | grep -qiE "\b(passwd|usermod|userdel|chage)\b.*\broot\b|deluser\s+root"; then
    echo "BLOCKED: You are not allowed to modify the root account." >&2
    exit 2
fi

# All clear
exit 0
