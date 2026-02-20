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

# ── Blocked patterns (everyone) ──────────────────────────────────────

# 1. Service management (bot, ouroboros, or any service stop/restart)
if echo "$CMD" | grep -qiE "systemctl|service\s+(stop|restart|start)|kill\s|pkill\s|killall\s|claude-telegram-bot|ouroboros"; then
    echo "BLOCKED: You are not allowed to manage system services. Use ./bin/restart.sh for the bot." >&2
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

# ── Non-admin additional restrictions ────────────────────────────────
if [ "$OPENCLAUDE_IS_ADMIN" != "1" ]; then
    WORKSPACE="${OPENCLAUDE_WORKSPACE:-}"

    # 7. Credential / env var snooping — block attempts to read host credentials
    if echo "$CMD" | grep -qiE "\benv\b|\bprintenv\b|/proc/.*environ|\bset\b\s*$|\bexport\s+-p\b"; then
        echo "BLOCKED: You are not allowed to inspect host environment variables." >&2
        exit 2
    fi
    if echo "$CMD" | grep -qiE "\.config/(gh|git)/|\.claude/\.credentials|\.netrc|\.npmrc|\.pypirc|/etc/shadow|\.ssh/|\.aws/|\.kube/"; then
        echo "BLOCKED: You are not allowed to access credential files." >&2
        exit 2
    fi
    # Block reading the project-level .env (host credentials)
    if echo "$CMD" | grep -qiE "cat.*/OpenClaude/\.env|head.*/OpenClaude/\.env|tail.*/OpenClaude/\.env|less.*/OpenClaude/\.env|more.*/OpenClaude/\.env"; then
        echo "BLOCKED: You are not allowed to read the host .env file." >&2
        exit 2
    fi

    # 9. chmod/chown on files outside workspace
    if [ -n "$WORKSPACE" ]; then
        if echo "$CMD" | grep -qiE "\b(chmod|chown)\b"; then
            # Extract paths from chmod/chown — block if any path is outside workspace
            # Simple heuristic: block if command doesn't reference workspace path
            if ! echo "$CMD" | grep -qF "$WORKSPACE"; then
                echo "BLOCKED: You can only change permissions on files within your workspace." >&2
                exit 2
            fi
        fi

        # 12. rm -rf on paths outside workspace
        if echo "$CMD" | grep -qiE "\brm\s+.*-[a-zA-Z]*r[a-zA-Z]*f|\brm\s+.*-[a-zA-Z]*f[a-zA-Z]*r"; then
            if ! echo "$CMD" | grep -qF "$WORKSPACE"; then
                echo "BLOCKED: You can only delete files within your workspace." >&2
                exit 2
            fi
        fi
    fi
fi

# All clear
exit 0
