"""Security rules (blocked patterns, permission handler, env building)."""

import os
import re
from pathlib import Path

from bot.config import ALL_TOOLS, CLAUDE_MODEL
from bot.logging_setup import logger

# Env vars safe to pass to non-admin users
_SAFE_ENV_KEYS = {
    "PATH", "HOME", "USER", "SHELL", "LANG", "LC_ALL", "LC_CTYPE",
    "TERM", "TMPDIR", "TMP", "TEMP", "XDG_CACHE_HOME", "XDG_CONFIG_HOME",
    "XDG_DATA_HOME", "XDG_RUNTIME_DIR", "EDITOR", "VISUAL", "PAGER",
    "PYTHONPATH", "NODE_PATH",
}

# Patterns blocked for ALL users
_BLOCKED_ALL_BASH = [
    (r"systemctl|service\s+(stop|restart|start)|kill\s|pkill\s|killall\s|claude-telegram-bot|ouroboros",
     "You are not allowed to manage system services. Use ./bin/restart.sh for the bot."),
    (r"sshd|ssh_config|authorized_keys|/etc/ssh",
     "You are not allowed to modify SSH configuration or keys."),
    (r"\b(iptables|ip6tables|nftables|nft|ufw)\b",
     "You are not allowed to modify firewall rules."),
    (r"\b(ifconfig|ip\s+(link|addr|route))\b.*\b(down|del|flush)\b|nmcli.*down|networkctl.*down",
     "You are not allowed to disable network interfaces."),
    (r"/etc/pam\.|/etc/nsswitch",
     "You are not allowed to modify PAM or NSS configuration."),
    (r"\b(passwd|usermod|userdel|chage)\b.*\broot\b|deluser\s+root",
     "You are not allowed to modify the root account."),
]

# Additional patterns blocked for non-admin
_BLOCKED_NONADMIN_BASH = [
    (r"\benv\b|\bprintenv\b|/proc/.*environ|\bset\b\s*$|\bexport\s+-p\b",
     "You are not allowed to inspect host environment variables."),
    (r"\.config/(gh|git)/|\.claude/\.credentials|\.netrc|\.npmrc|\.pypirc|/etc/shadow|\.ssh/|\.aws/|\.kube/",
     "You are not allowed to access credential files."),
    (r"cat.*/OpenClaude/\.env|head.*/OpenClaude/\.env|tail.*/OpenClaude/\.env|less.*/OpenClaude/\.env|more.*/OpenClaude/\.env",
     "You are not allowed to read the host .env file."),
]

# Protected file paths for Write/Edit
_BLOCKED_WRITE_PATHS = re.compile(
    r"/etc/ssh|authorized_keys|known_hosts|/etc/pam\.|/etc/nsswitch"
    r"|/etc/shadow|/etc/passwd|/etc/iptables|/etc/nftables|/etc/ufw"
    r"|guard\.sh|guard-write\.sh",
    re.IGNORECASE,
)


def load_workspace_env(workspace_dir: str) -> dict[str, str]:
    """Load env vars from a workspace's .env file, if it exists."""
    env_file = Path(workspace_dir) / ".env"
    if not env_file.exists():
        return {}
    result = {}
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("'\"")
            if key:
                result[key] = value
    return result


def build_env(is_admin: bool, cwd: str, thread_id: int) -> dict[str, str]:
    """Build the environment dict for a Claude subprocess."""
    if is_admin:
        env = os.environ.copy()
    else:
        env = {k: v for k, v in os.environ.items() if k in _SAFE_ENV_KEYS}

    env.pop("CLAUDECODE", None)

    local_bin = str(Path.home() / ".local" / "bin")
    if local_bin not in env.get("PATH", ""):
        env["PATH"] = local_bin + ":" + env.get("PATH", "/usr/bin:/bin")

    workspace_env = load_workspace_env(cwd)
    env.update(workspace_env)

    env["IS_SANDBOX"] = "1"
    env["OPENCLAUDE_IS_ADMIN"] = "1" if is_admin else "0"
    env["OPENCLAUDE_WORKSPACE"] = cwd
    env["OPENCLAUDE_THREAD_ID"] = str(thread_id)

    return env


def make_permission_handler(is_admin: bool, workspace: str):
    """Build a can_use_tool callback that mirrors guard.sh / guard-write.sh logic."""
    from bot.sdk_session import PermissionResultAllow, PermissionResultDeny

    async def handler(tool_name, input_data, context):
        if tool_name == "Bash":
            cmd = input_data.get("command", "")
            if not cmd:
                return PermissionResultAllow(updated_input=input_data)

            for pattern, msg in _BLOCKED_ALL_BASH:
                if re.search(pattern, cmd, re.IGNORECASE):
                    return PermissionResultDeny(message=f"BLOCKED: {msg}")

            if not is_admin:
                for pattern, msg in _BLOCKED_NONADMIN_BASH:
                    if re.search(pattern, cmd, re.IGNORECASE):
                        return PermissionResultDeny(message=f"BLOCKED: {msg}")

                if re.search(r"\b(chmod|chown)\b", cmd, re.IGNORECASE):
                    if workspace not in cmd:
                        return PermissionResultDeny(
                            message="BLOCKED: You can only change permissions on files within your workspace.")

                if re.search(r"\brm\s+.*-[a-zA-Z]*r[a-zA-Z]*f|\brm\s+.*-[a-zA-Z]*f[a-zA-Z]*r", cmd, re.IGNORECASE):
                    if workspace not in cmd:
                        return PermissionResultDeny(
                            message="BLOCKED: You can only delete files within your workspace.")

        if tool_name in ("Write", "Edit"):
            filepath = input_data.get("file_path", "")
            if filepath:
                if not is_admin and workspace:
                    real_path = os.path.realpath(filepath)
                    if not real_path.startswith(workspace + "/") and real_path != workspace:
                        return PermissionResultDeny(
                            message="BLOCKED: You can only modify files within your workspace.")

                if _BLOCKED_WRITE_PATHS.search(filepath):
                    return PermissionResultDeny(
                        message=f"BLOCKED: You are not allowed to modify this protected file: {filepath}")

        return PermissionResultAllow(updated_input=input_data)

    return handler


def build_sdk_options(is_admin: bool, cwd: str, thread_id: int,
                      session_id: str | None, streaming: bool):
    """Build ClaudeCodeOptions for an SDK session."""
    from bot.sdk_session import ClaudeCodeOptions
    env = build_env(is_admin, cwd, thread_id)
    return ClaudeCodeOptions(
        allowed_tools=ALL_TOOLS.split(","),
        permission_mode="bypassPermissions",
        cwd=cwd,
        resume=session_id or None,
        model=CLAUDE_MODEL or None,
        env=env,
        include_partial_messages=streaming,
        can_use_tool=make_permission_handler(is_admin, cwd),
    )
