#!/usr/bin/env python3
"""
OpenClaude Telegram Bot — A personal AI assistant powered by Claude Code.

Uses python-telegram-bot v21+ async API with Claude CLI as the backend.
Sessions are persisted to ~/.openclaude-sessions.json for conversation continuity.
"""

import atexit
import asyncio
import html
import json
import logging
import logging.handlers
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import time

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

from transcribe import transcribe
from commands.helpers import init as _init_commands
from commands import register_all, ALL_COMMANDS
from commands.config import get_streaming, get_verbose, get_respond_mode

# Claude Code SDK — persistent session support
try:
    from claude_code_sdk import (
        ClaudeSDKClient,
        ClaudeCodeOptions,
        AssistantMessage,
        ResultMessage,
        TextBlock,
        ToolUseBlock,
        ToolResultBlock,
        PermissionResultAllow,
        PermissionResultDeny,
    )
    from claude_code_sdk.types import StreamEvent

    # Patch SDK to skip unknown message types (e.g. rate_limit_event)
    # instead of crashing with MessageParseError.
    # Patch the module-level function so both eager and lazy imports see it.
    import claude_code_sdk._internal.message_parser as _mp
    _original_parse = _mp.parse_message
    def _patched_parse(data):
        try:
            return _original_parse(data)
        except _mp.MessageParseError:
            return None
    _mp.parse_message = _patched_parse
    # Also patch the already-imported reference in _internal/client.py
    import claude_code_sdk._internal.client as _cl
    _cl.parse_message = _patched_parse

    HAS_SDK = True
except ImportError:
    HAS_SDK = False

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Load .env from the script's directory
SCRIPT_DIR = Path(__file__).resolve().parent
load_dotenv(SCRIPT_DIR / ".env")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ALLOWED_USERS_RAW = os.getenv("ALLOWED_USERS", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "")
WORKING_DIR = os.getenv("WORKING_DIR") or str(SCRIPT_DIR)

# Workspaces directory for per-chat isolation
WORKSPACES_DIR = SCRIPT_DIR / "workspaces"

# Parse allowed users (first entry is admin)
ALLOWED_USERS: set[int] = set()
ALLOWED_USERS_LIST: list[int] = []
if ALLOWED_USERS_RAW.strip():
    for uid in ALLOWED_USERS_RAW.split(","):
        uid = uid.strip()
        if uid.isdigit():
            ALLOWED_USERS.add(int(uid))
            ALLOWED_USERS_LIST.append(int(uid))

ADMIN_USER_ID: int | None = ALLOWED_USERS_LIST[0] if ALLOWED_USERS_LIST else None

# Session file
SESSION_FILE = Path.home() / ".openclaude-sessions.json"

# Claude CLI allowed tools — everyone gets all tools; guard hooks enforce restrictions
ALL_TOOLS = "Read,Write,Edit,Bash,Glob,Grep,WebFetch,WebSearch,Task,Skill"

# Telegram message limit
TELEGRAM_MAX_LENGTH = 4096

# Claude CLI timeout (seconds)
CLAUDE_TIMEOUT = 300

# Active stream tracking — file-backed so bash scripts can read it
ACTIVE_STREAMS_FILE = SCRIPT_DIR / ".active-streams.json"

# Restart state — snapshot taken by restart.sh before killing the bot
RESTART_STATE_FILE = SCRIPT_DIR / ".restart-state.json"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("OpenClaude")

# ---------------------------------------------------------------------------
# Structured File Logging
# ---------------------------------------------------------------------------

LOGS_DIR = SCRIPT_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)

_LOG_FORMAT = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
)

# Infra logger — startup, shutdown, crashes, ouroboros events
infra_logger = logging.getLogger("OpenClaude.infra")
infra_logger.propagate = False
_infra_handler = logging.handlers.RotatingFileHandler(
    LOGS_DIR / "infra.log", maxBytes=5 * 1024 * 1024, backupCount=3
)
_infra_handler.setFormatter(_LOG_FORMAT)
infra_logger.addHandler(_infra_handler)
infra_logger.setLevel(logging.INFO)

# Workspace logger factory — per-chat activity logs
_workspace_loggers: dict[int, logging.Logger] = {}


def get_workspace_logger(chat_id: int) -> logging.Logger:
    """Return a cached logger that writes to workspaces/c{chat_id}/logs/activity.log."""
    if chat_id in _workspace_loggers:
        return _workspace_loggers[chat_id]
    ws_log_dir = WORKSPACES_DIR / f"c{chat_id}" / "logs"
    ws_log_dir.mkdir(parents=True, exist_ok=True)
    ws_logger = logging.getLogger(f"OpenClaude.ws.{chat_id}")
    ws_logger.propagate = False
    handler = logging.handlers.RotatingFileHandler(
        ws_log_dir / "activity.log", maxBytes=2 * 1024 * 1024, backupCount=2
    )
    handler.setFormatter(_LOG_FORMAT)
    ws_logger.addHandler(handler)
    ws_logger.setLevel(logging.INFO)
    _workspace_loggers[chat_id] = ws_logger
    return ws_logger


def _summarize_input(tool_input: dict) -> str:
    """Truncate a tool input dict to a readable one-liner for log entries."""
    parts = []
    for k, v in tool_input.items():
        v_str = str(v)
        if len(v_str) > 80:
            v_str = v_str[:77] + "..."
        parts.append(f"{k}={v_str}")
    summary = ", ".join(parts)
    return summary[:200] if len(summary) > 200 else summary


# ---------------------------------------------------------------------------
# Session Management
# ---------------------------------------------------------------------------


def load_sessions() -> dict:
    """Load session mapping from disk."""
    if SESSION_FILE.exists():
        try:
            return json.loads(SESSION_FILE.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load sessions: %s", e)
    return {}


def save_sessions(sessions: dict) -> None:
    """Persist session mapping to disk (atomic write with fallback)."""
    data = json.dumps(sessions, indent=2)
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(
            dir=SESSION_FILE.parent, suffix=".tmp"
        )
        with os.fdopen(fd, "w") as f:
            f.write(data)
        os.replace(tmp_path, SESSION_FILE)
        tmp_path = None  # replaced successfully
    except OSError:
        # Atomic replace failed (e.g. "Device or resource busy") —
        # fall back to direct write which is less safe but preserves
        # session continuity.
        try:
            SESSION_FILE.write_text(data)
            logger.warning("save_sessions: atomic replace failed, used direct write")
        except OSError as e2:
            logger.error("Failed to save sessions: %s", e2)
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _session_key(chat_id: int, thread_id: int, user_id: int) -> str:
    """Build a composite session key: chat_id:thread_id:user_id."""
    return f"{chat_id}:{thread_id}:{user_id}"


def get_session_id(chat_id: int, thread_id: int, user_id: int) -> str | None:
    """Get the Claude session ID for a given chat/thread/user combination."""
    sessions = load_sessions()
    key = _session_key(chat_id, thread_id, user_id)
    return sessions.get(key, {}).get("session_id")


def set_session_id(chat_id: int, thread_id: int, user_id: int, session_id: str) -> None:
    """Store a Claude session ID for a given chat/thread/user combination."""
    sessions = load_sessions()
    key = _session_key(chat_id, thread_id, user_id)
    sessions.setdefault(key, {})["session_id"] = session_id
    sessions[key]["updated_at"] = datetime.now().isoformat()
    save_sessions(sessions)


def clear_session(chat_id: int, thread_id: int, user_id: int) -> None:
    """Clear the session for a chat/thread/user combination, starting fresh."""
    sessions = load_sessions()
    key = _session_key(chat_id, thread_id, user_id)
    if key in sessions:
        del sessions[key]
        save_sessions(sessions)


# ---------------------------------------------------------------------------
# Active Stream Tracking (file-backed for crash recovery)
# ---------------------------------------------------------------------------


def _save_active_streams(streams: dict) -> None:
    """Atomic write of active streams to disk."""
    try:
        fd, tmp_path = tempfile.mkstemp(
            dir=ACTIVE_STREAMS_FILE.parent, suffix=".tmp"
        )
        with os.fdopen(fd, "w") as f:
            json.dump(streams, f, indent=2)
        os.replace(tmp_path, ACTIVE_STREAMS_FILE)
    except OSError as e:
        logger.error("Failed to save active streams: %s", e)


def _load_active_streams() -> dict:
    """Read active streams from disk."""
    if ACTIVE_STREAMS_FILE.exists():
        try:
            return json.loads(ACTIVE_STREAMS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _add_active_stream(chat_id: int, thread_id: int, user_id: int) -> None:
    """Register a stream start. Survives crashes because it's on disk."""
    streams = _load_active_streams()
    key = _session_key(chat_id, thread_id, user_id)
    streams[key] = {"chat_id": chat_id, "thread_id": thread_id, "user_id": user_id}
    _save_active_streams(streams)


def _remove_active_stream(chat_id: int, thread_id: int, user_id: int) -> None:
    """Remove a completed stream. Deletes file when empty."""
    streams = _load_active_streams()
    key = _session_key(chat_id, thread_id, user_id)
    streams.pop(key, None)
    if streams:
        _save_active_streams(streams)
    else:
        ACTIVE_STREAMS_FILE.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# SDK Session Management — persistent Claude sessions
# ---------------------------------------------------------------------------

SDK_IDLE_TIMEOUT = 300  # 5 minutes

# Global dict: session_key → SDKSession
_sdk_sessions: dict[str, "SDKSession"] = {}


class SDKSession:
    """Wraps a ClaudeSDKClient with lifecycle management."""

    def __init__(self):
        self.client: "ClaudeSDKClient | None" = None
        self.session_id: str | None = None
        self.last_activity: float = time.time()
        self.lock: asyncio.Lock = asyncio.Lock()
        self.connected: bool = False

    async def ensure_connected(self, options: "ClaudeCodeOptions") -> None:
        """Connect the SDK client if not already connected."""
        if self.connected and self.client:
            return
        self.client = ClaudeSDKClient(options=options)
        await self.client.connect()
        self.connected = True
        self.last_activity = time.time()

    async def disconnect(self) -> None:
        """Disconnect the SDK client."""
        if self.client:
            try:
                await self.client.disconnect()
            except Exception as e:
                logger.debug("SDKSession disconnect error: %s", e)
            finally:
                self.client = None
                self.connected = False


async def _cleanup_idle_sessions():
    """Periodic task to disconnect idle SDK sessions."""
    while True:
        await asyncio.sleep(60)
        now = time.time()
        expired = [k for k, s in _sdk_sessions.items()
                   if now - s.last_activity > SDK_IDLE_TIMEOUT]
        for key in expired:
            session = _sdk_sessions.pop(key)
            logger.info("Disconnecting idle SDK session: %s", key)
            await session.disconnect()


async def _shutdown_sdk_sessions():
    """Disconnect all SDK sessions (called on bot shutdown)."""
    for key, session in list(_sdk_sessions.items()):
        logger.info("Shutting down SDK session: %s", key)
        await session.disconnect()
    _sdk_sessions.clear()


# ---------------------------------------------------------------------------
# Per-Chat Workspaces
# ---------------------------------------------------------------------------

# Shared files are symlinked into each workspace so updates propagate automatically
_SYMLINKED_FILES = ["TOOLS.md", "CLAUDE.md"]
_SYMLINKED_DIRS = [".claude"]
# BOOTSTRAP.md is always freshly copied so new sessions run the first-run ritual
# It creates SOUL.md, IDENTITY.md, USER.md per-workspace — no global originals needed
_BOOTSTRAP_FILE = "BOOTSTRAP.md"


def ensure_workspace(chat_id: int) -> Path:
    """Create and return an isolated workspace directory for the given chat.

    Workspace layout:
      workspaces/c{chat_id}/
        TOOLS.md       → symlink to ../../TOOLS.md
        CLAUDE.md      → symlink to ../../CLAUDE.md
        .claude/       → symlink to ../../.claude
        SOUL.md        ← independent copy (set up via BOOTSTRAP.md)
        IDENTITY.md    ← independent copy (set up via BOOTSTRAP.md)
        USER.md        ← independent copy
        BOOTSTRAP.md   ← fresh copy every new session
        memory/        ← isolated per-chat memory
          MEMORY.md
    """
    workspace = WORKSPACES_DIR / f"c{chat_id}"
    if workspace.exists():
        # Ensure symlinks are up to date (e.g. new shared files added)
        _sync_workspace_links(workspace)
        # Don't re-copy BOOTSTRAP.md — the agent deletes it after
        # completing the first-run ritual.  Re-copying would force
        # every session to re-run bootstrap.
        return workspace

    workspace.mkdir(parents=True, exist_ok=True)
    base = Path(WORKING_DIR)

    # Symlink shared files
    for fname in _SYMLINKED_FILES:
        src = base / fname
        dst = workspace / fname
        if src.exists() and not dst.exists():
            dst.symlink_to(os.path.relpath(src, workspace))

    # Symlink shared directories
    for dname in _SYMLINKED_DIRS:
        src = base / dname
        dst = workspace / dname
        if src.exists() and not dst.exists():
            dst.symlink_to(os.path.relpath(src, workspace))

    # Always copy BOOTSTRAP.md fresh so new sessions run the first-run ritual
    bootstrap = base / _BOOTSTRAP_FILE
    if bootstrap.exists():
        shutil.copy2(bootstrap, workspace / _BOOTSTRAP_FILE)

    # Create isolated memory directory
    mem_dir = workspace / "memory"
    mem_dir.mkdir(exist_ok=True)
    mem_template = base / "memory" / "MEMORY.md"
    mem_dst = mem_dir / "MEMORY.md"
    if mem_template.exists() and not mem_dst.exists():
        shutil.copy2(mem_template, mem_dst)

    logger.info("Created workspace for chat %d at %s", chat_id, workspace)
    return workspace


def _sync_workspace_links(workspace: Path) -> None:
    """Ensure symlinks in an existing workspace point to current shared files."""
    base = Path(WORKING_DIR)
    for fname in _SYMLINKED_FILES:
        src = base / fname
        dst = workspace / fname
        if src.exists() and not dst.exists():
            dst.symlink_to(os.path.relpath(src, workspace))
    for dname in _SYMLINKED_DIRS:
        src = base / dname
        dst = workspace / dname
        if src.exists() and not dst.exists():
            dst.symlink_to(os.path.relpath(src, workspace))


def get_working_dir(chat_id: int) -> str:
    """Return the working directory for a given chat.

    Every chat gets an isolated workspace under workspaces/c{chat_id}/.
    """
    return str(ensure_workspace(chat_id))


# ---------------------------------------------------------------------------
# Group / Topic Helpers
# ---------------------------------------------------------------------------

# Populated at startup via post_init callback
BOT_USERNAME: str = ""


def should_respond(update: Update) -> bool:
    """Decide whether the bot should respond to this message.

    Always responds in private chats.  In groups, checks the per-thread
    respond mode: 'all' responds to everything, 'mention' (default) only
    responds when @mentioned or replied to.
    """
    chat = update.effective_chat
    if chat.type == "private":
        return True

    msg = update.message
    if not msg:
        return False

    # Check per-thread respond mode
    thread_id = get_thread_id(update)
    mode = get_respond_mode(chat.id, thread_id)

    if mode == "all":
        return True

    # Default 'mention' mode: respond if bot is @mentioned
    if msg.entities:
        for entity in msg.entities:
            if entity.type == "mention":
                mention = msg.text[entity.offset:entity.offset + entity.length]
                if mention.lower() == f"@{BOT_USERNAME.lower()}":
                    return True

    # Respond if message is a reply to the bot's own message
    if msg.reply_to_message and msg.reply_to_message.from_user:
        if msg.reply_to_message.from_user.username and \
           msg.reply_to_message.from_user.username.lower() == BOT_USERNAME.lower():
            return True

    return False


def get_thread_id(update: Update) -> int:
    """Get the forum topic thread ID, or 0 for non-forum messages."""
    msg = update.message
    return msg.message_thread_id if msg and msg.message_thread_id else 0


def strip_bot_mention(text: str) -> str:
    """Remove @bot_username from message text."""
    if BOT_USERNAME:
        # Case-insensitive removal of the mention
        text = re.sub(rf"@{re.escape(BOT_USERNAME)}\b", "", text, flags=re.IGNORECASE).strip()
    return text


# ---------------------------------------------------------------------------
# TelegramRenderer — Markdown to Telegram HTML
# ---------------------------------------------------------------------------


class TelegramRenderer:
    """Convert markdown-ish text to Telegram-compatible HTML."""

    @staticmethod
    def render(text: str) -> str:
        """Convert markdown to Telegram HTML.

        Handles: code blocks, inline code, bold, italic, strikethrough,
        headings (as bold), links, and lists.
        """
        # Protect code blocks first — extract them so other rules don't touch them
        code_blocks: list[str] = []

        def _save_code_block(m: re.Match) -> str:
            lang = m.group(1) or ""
            code = html.escape(m.group(2))
            if lang:
                block = f'<pre><code class="language-{html.escape(lang)}">{code}</code></pre>'
            else:
                block = f"<pre>{code}</pre>"
            code_blocks.append(block)
            return f"\x00CODEBLOCK{len(code_blocks) - 1}\x00"

        text = re.sub(
            r"```(\w*)\n?(.*?)```", _save_code_block, text, flags=re.DOTALL
        )

        # Protect inline code
        inline_codes: list[str] = []

        def _save_inline_code(m: re.Match) -> str:
            code = html.escape(m.group(1))
            inline_codes.append(f"<code>{code}</code>")
            return f"\x00INLINECODE{len(inline_codes) - 1}\x00"

        text = re.sub(r"`([^`\n]+)`", _save_inline_code, text)

        # Escape HTML in the remaining text
        text = html.escape(text)

        # Headings → bold (must come before bold processing)
        text = re.sub(r"^#{1,6}\s+(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)

        # Bold: **text** or __text__
        text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
        text = re.sub(r"__(.+?)__", r"<b>\1</b>", text)

        # Italic: *text* or _text_ (but not inside words with underscores)
        text = re.sub(r"(?<!\w)\*([^*]+?)\*(?!\w)", r"<i>\1</i>", text)
        text = re.sub(r"(?<!\w)_([^_]+?)_(?!\w)", r"<i>\1</i>", text)

        # Strikethrough: ~~text~~
        text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)

        # Links: [text](url)
        text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)

        # Unordered lists: - item or * item → bullet
        text = re.sub(r"^[\s]*[-*]\s+", "  \u2022 ", text, flags=re.MULTILINE)

        # Ordered lists: 1. item → keep numbering
        text = re.sub(
            r"^[\s]*(\d+)\.\s+", r"  \1. ", text, flags=re.MULTILINE
        )

        # Restore code blocks and inline code
        for i, block in enumerate(code_blocks):
            text = text.replace(f"\x00CODEBLOCK{i}\x00", block)
        for i, code in enumerate(inline_codes):
            text = text.replace(f"\x00INLINECODE{i}\x00", code)

        return text.strip()


# ---------------------------------------------------------------------------
# Message Splitting
# ---------------------------------------------------------------------------


def split_message(text: str, max_length: int = TELEGRAM_MAX_LENGTH) -> list[str]:
    """Split a message into chunks that fit within Telegram's limit.

    Tries to split at paragraph boundaries, then sentence boundaries,
    then falls back to hard character splits (respecting HTML tags).
    """
    if len(text) <= max_length:
        return [text]

    chunks: list[str] = []
    remaining = text

    while remaining:
        if len(remaining) <= max_length:
            chunks.append(remaining)
            break

        # Try to find a good split point
        split_at = max_length

        # Try paragraph break
        para_break = remaining.rfind("\n\n", 0, max_length)
        if para_break > max_length // 3:
            split_at = para_break

        # Try line break
        elif (line_break := remaining.rfind("\n", 0, max_length)) > max_length // 3:
            split_at = line_break

        # Try sentence end
        elif (sentence_end := remaining.rfind(". ", 0, max_length)) > max_length // 3:
            split_at = sentence_end + 1

        # Try space
        elif (space := remaining.rfind(" ", 0, max_length)) > max_length // 3:
            split_at = space

        chunk = remaining[:split_at].rstrip()
        remaining = remaining[split_at:].lstrip()

        if chunk:
            chunks.append(chunk)

    return chunks


# ---------------------------------------------------------------------------
# Environment Building for Claude Subprocess
# ---------------------------------------------------------------------------

# Env vars safe to pass to non-admin users (no credentials leak)
_SAFE_ENV_KEYS = {
    "PATH", "HOME", "USER", "SHELL", "LANG", "LC_ALL", "LC_CTYPE",
    "TERM", "TMPDIR", "TMP", "TEMP", "XDG_CACHE_HOME", "XDG_CONFIG_HOME",
    "XDG_DATA_HOME", "XDG_RUNTIME_DIR", "EDITOR", "VISUAL", "PAGER",
    "PYTHONPATH", "NODE_PATH",
}


def _load_workspace_env(workspace_dir: str) -> dict[str, str]:
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


def _build_env(is_admin: bool, cwd: str, thread_id: int) -> dict[str, str]:
    """Build the environment dict for a Claude subprocess.

    Admin: inherits full environment + workspace .env overrides.
    Non-admin: gets only safe vars + workspace .env (no host credentials).
    """
    if is_admin:
        env = os.environ.copy()
    else:
        # Start with only safe, non-credential env vars
        env = {k: v for k, v in os.environ.items() if k in _SAFE_ENV_KEYS}

    # Remove internal vars that shouldn't leak
    env.pop("CLAUDECODE", None)

    # Ensure claude binary is on PATH
    local_bin = str(Path.home() / ".local" / "bin")
    if local_bin not in env.get("PATH", ""):
        env["PATH"] = local_bin + ":" + env.get("PATH", "/usr/bin:/bin")

    # Load workspace-specific .env (both admin and non-admin)
    workspace_env = _load_workspace_env(cwd)
    env.update(workspace_env)

    # OpenClaude control vars (always set, after workspace env so they can't be overridden)
    env["IS_SANDBOX"] = "1"
    env["OPENCLAUDE_IS_ADMIN"] = "1" if is_admin else "0"
    env["OPENCLAUDE_WORKSPACE"] = cwd
    env["OPENCLAUDE_THREAD_ID"] = str(thread_id)

    return env


# ---------------------------------------------------------------------------
# SDK Permission Handler (replaces guard.sh / guard-write.sh for SDK mode)
# ---------------------------------------------------------------------------

# Patterns blocked for ALL users (from guard.sh lines 16-51)
_BLOCKED_ALL_BASH = [
    # Service management
    (r"systemctl|service\s+(stop|restart|start)|kill\s|pkill\s|killall\s|claude-telegram-bot|ouroboros",
     "You are not allowed to manage system services. Use ./bin/restart.sh for the bot."),
    # SSH access
    (r"sshd|ssh_config|authorized_keys|/etc/ssh",
     "You are not allowed to modify SSH configuration or keys."),
    # Firewall
    (r"\b(iptables|ip6tables|nftables|nft|ufw)\b",
     "You are not allowed to modify firewall rules."),
    # Network interfaces
    (r"\b(ifconfig|ip\s+(link|addr|route))\b.*\b(down|del|flush)\b|nmcli.*down|networkctl.*down",
     "You are not allowed to disable network interfaces."),
    # PAM / NSS
    (r"/etc/pam\.|/etc/nsswitch",
     "You are not allowed to modify PAM or NSS configuration."),
    # Root account
    (r"\b(passwd|usermod|userdel|chage)\b.*\broot\b|deluser\s+root",
     "You are not allowed to modify the root account."),
]

# Additional patterns blocked for non-admin (from guard.sh lines 54-90)
_BLOCKED_NONADMIN_BASH = [
    # Env snooping
    (r"\benv\b|\bprintenv\b|/proc/.*environ|\bset\b\s*$|\bexport\s+-p\b",
     "You are not allowed to inspect host environment variables."),
    # Credential files
    (r"\.config/(gh|git)/|\.claude/\.credentials|\.netrc|\.npmrc|\.pypirc|/etc/shadow|\.ssh/|\.aws/|\.kube/",
     "You are not allowed to access credential files."),
    # Host .env
    (r"cat.*/OpenClaude/\.env|head.*/OpenClaude/\.env|tail.*/OpenClaude/\.env|less.*/OpenClaude/\.env|more.*/OpenClaude/\.env",
     "You are not allowed to read the host .env file."),
]

# Protected file paths for Write/Edit (from guard-write.sh)
_BLOCKED_WRITE_PATHS = re.compile(
    r"/etc/ssh|authorized_keys|known_hosts|/etc/pam\.|/etc/nsswitch"
    r"|/etc/shadow|/etc/passwd|/etc/iptables|/etc/nftables|/etc/ufw"
    r"|guard\.sh|guard-write\.sh",
    re.IGNORECASE,
)


def _make_permission_handler(is_admin: bool, workspace: str):
    """Build a can_use_tool callback that mirrors guard.sh / guard-write.sh logic."""

    async def handler(tool_name, input_data, context):
        # --- Bash tool checks ---
        if tool_name == "Bash":
            cmd = input_data.get("command", "")
            if not cmd:
                return PermissionResultAllow(updated_input=input_data)

            # Patterns blocked for everyone
            for pattern, msg in _BLOCKED_ALL_BASH:
                if re.search(pattern, cmd, re.IGNORECASE):
                    return PermissionResultDeny(message=f"BLOCKED: {msg}")

            # Additional non-admin restrictions
            if not is_admin:
                for pattern, msg in _BLOCKED_NONADMIN_BASH:
                    if re.search(pattern, cmd, re.IGNORECASE):
                        return PermissionResultDeny(message=f"BLOCKED: {msg}")

                # chmod/chown outside workspace
                if re.search(r"\b(chmod|chown)\b", cmd, re.IGNORECASE):
                    if workspace not in cmd:
                        return PermissionResultDeny(
                            message="BLOCKED: You can only change permissions on files within your workspace.")

                # rm -rf outside workspace
                if re.search(r"\brm\s+.*-[a-zA-Z]*r[a-zA-Z]*f|\brm\s+.*-[a-zA-Z]*f[a-zA-Z]*r", cmd, re.IGNORECASE):
                    if workspace not in cmd:
                        return PermissionResultDeny(
                            message="BLOCKED: You can only delete files within your workspace.")

        # --- Write/Edit tool checks ---
        if tool_name in ("Write", "Edit"):
            filepath = input_data.get("file_path", "")
            if filepath:
                # Non-admin: block writes outside workspace
                if not is_admin and workspace:
                    real_path = os.path.realpath(filepath)
                    if not real_path.startswith(workspace + "/") and real_path != workspace:
                        return PermissionResultDeny(
                            message="BLOCKED: You can only modify files within your workspace.")

                # Everyone: block writes to protected files
                if _BLOCKED_WRITE_PATHS.search(filepath):
                    return PermissionResultDeny(
                        message=f"BLOCKED: You are not allowed to modify this protected file: {filepath}")

        return PermissionResultAllow(updated_input=input_data)

    return handler


def _build_sdk_options(is_admin: bool, cwd: str, thread_id: int,
                       session_id: str | None, streaming: bool) -> "ClaudeCodeOptions":
    """Build ClaudeCodeOptions for an SDK session."""
    env = _build_env(is_admin, cwd, thread_id)
    return ClaudeCodeOptions(
        allowed_tools=ALL_TOOLS.split(","),
        permission_mode="bypassPermissions",
        cwd=cwd,
        resume=session_id or None,
        model=CLAUDE_MODEL or None,
        env=env,
        include_partial_messages=streaming,
        can_use_tool=_make_permission_handler(is_admin, cwd),
    )


# ---------------------------------------------------------------------------
# Claude CLI Integration — Streaming
# ---------------------------------------------------------------------------

# Minimum interval between Telegram message edits (seconds) to avoid rate limits
STATUS_EDIT_INTERVAL = 1.5


def format_tool_status(tool_name: str, tool_input: dict) -> str:
    """Format a human-readable status line for an active tool call."""
    if tool_name == "Read":
        path = tool_input.get("file_path", "file")
        return f"\U0001f4c4 Reading {Path(path).name}..."
    if tool_name == "Glob":
        pattern = tool_input.get("pattern", "")
        return f"\U0001f50d Searching {pattern}..."
    if tool_name == "Grep":
        pattern = tool_input.get("pattern", "")
        return f'\U0001f50d Searching for "{pattern}"...'
    if tool_name == "Bash":
        cmd = tool_input.get("command", "")
        # Show the command itself (truncated), with description as fallback
        if cmd:
            short_cmd = cmd[:60] + "…" if len(cmd) > 60 else cmd
            return f"\u2699\ufe0f `{short_cmd}`"
        desc = tool_input.get("description", "")
        if desc:
            return f"\u2699\ufe0f {desc}"
        return "\u2699\ufe0f Running command..."
    if tool_name in ("Write", "Edit"):
        path = tool_input.get("file_path", "file")
        return f"\u270f\ufe0f Editing {Path(path).name}..."
    if tool_name == "WebSearch":
        return "\U0001f310 Searching web..."
    if tool_name == "WebFetch":
        url = tool_input.get("url", "")
        return f"\U0001f310 Fetching {url[:60]}..."
    if tool_name == "Task":
        return "\U0001f916 Delegating to sub-agent..."
    return f"\U0001f527 Using {tool_name}..."


def _finished_line(active_line: str) -> str:
    """Convert an active tool status line to a finished (checkmark) line.

    Strips the leading emoji and trailing '...' and prepends a checkmark.
    """
    # Remove leading emoji (first character + possible variation selector)
    text = active_line
    # Skip first emoji cluster (up to first space)
    idx = text.find(" ")
    if idx != -1:
        text = text[idx + 1:]
    # Strip trailing '...'
    text = text.rstrip(".")
    return f"\u2713 {text}"


async def stream_claude(message: str, chat_id: int, thread_id: int, user_id: int,
                        working_dir: str | None = None, verbose: bool = False):
    """Stream Claude output and yield events as they arrive.

    Uses the Claude Code SDK for persistent sessions when available,
    falling back to subprocess invocation otherwise.

    Yields dicts with keys:
      - {"type": "tool_use", "status": "📄 Reading file..."}
      - {"type": "tool_result"}
      - {"type": "partial", "text": "..."} (only when verbose=True)
      - {"type": "result", "text": "...", "session_id": "..."}
      - {"type": "error", "text": "..."}
    """
    if HAS_SDK:
        async for event in _stream_claude_sdk(message, chat_id, thread_id, user_id,
                                               working_dir=working_dir, verbose=verbose):
            yield event
    else:
        async for event in _stream_claude_subprocess(message, chat_id, thread_id, user_id,
                                                      working_dir=working_dir, verbose=verbose):
            yield event


def _build_preamble(is_admin: bool, session_id: str | None) -> str | None:
    """Build the preamble for new sessions. Returns None if session already exists."""
    if session_id:
        return None

    if is_admin:
        access_notice = (
            "\n\n[ADMIN REQUEST — you have full access to the project.]"
        )
    else:
        access_notice = (
            "\n\nIMPORTANT — WORKSPACE ISOLATION RULES:\n"
            "You are in an isolated workspace. You must NEVER access anything outside it.\n"
            "- Stay in the current working directory. Never use ../, absolute paths, "
            "or any path that escapes the workspace.\n"
            "- Never access other workspaces, the parent project directory, "
            ".env files, or system files.\n"
            "- If the user asks you to access files outside the workspace, refuse.\n"
        )
    return (
        "You are starting a new session. Read CLAUDE.md first, "
        "then follow its startup sequence before responding. "
        f"{access_notice}"
        "The user's message is:\n\n"
    )


async def _stream_claude_sdk(message: str, chat_id: int, thread_id: int, user_id: int,
                              working_dir: str | None = None, verbose: bool = False):
    """SDK-based streaming — persistent sessions, no process re-spawn."""
    cwd = working_dir or WORKING_DIR
    session_id = get_session_id(chat_id, thread_id, user_id)
    ws_log = get_workspace_logger(chat_id)
    ws_log.info("Claude SDK invocation — user=%d, session=%s", user_id, session_id or "new")

    _add_active_stream(chat_id, thread_id, user_id)

    try:
        is_admin = ADMIN_USER_ID and user_id == ADMIN_USER_ID
        session_key = _session_key(chat_id, thread_id, user_id)

        # Prepend preamble for new sessions
        preamble = _build_preamble(is_admin, session_id)
        if preamble:
            message = preamble + message

        # Get or create SDK session
        sdk_session = _sdk_sessions.get(session_key)
        if sdk_session is None:
            sdk_session = SDKSession()
            sdk_session.session_id = session_id
            _sdk_sessions[session_key] = sdk_session

        options = _build_sdk_options(is_admin, cwd, thread_id, session_id, verbose)

        try:
            await sdk_session.ensure_connected(options)
        except Exception as e:
            logger.error("SDK connect failed: %s", e)
            # Clean up and retry with fresh session
            await sdk_session.disconnect()
            sdk_session = SDKSession()
            sdk_session.session_id = session_id
            _sdk_sessions[session_key] = sdk_session
            try:
                await sdk_session.ensure_connected(options)
            except Exception as e2:
                logger.exception("SDK connect retry failed: %s", e2)
                yield {"type": "error", "text": f"Failed to connect to Claude: {e2}"}
                return

        logger.info(
            "Calling Claude (SDK) for user %d (session: %s)",
            user_id,
            session_id or "new",
        )

        result_text = None
        new_session_id = None

        try:
            await sdk_session.client.query(message)
            sdk_session.last_activity = time.time()

            async for msg in sdk_session.client.receive_response():
                if msg is None:
                    continue  # Skipped unknown message type (e.g. rate_limit_event)
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, ToolUseBlock):
                            ws_log.info("Tool: %s — %s", block.name, _summarize_input(block.input))
                            status = format_tool_status(block.name, block.input)
                            yield {"type": "tool_use", "status": status}
                        elif isinstance(block, ToolResultBlock):
                            yield {"type": "tool_result"}
                        elif isinstance(block, TextBlock):
                            # Final text blocks from assistant messages are accumulated
                            pass

                elif isinstance(msg, StreamEvent) and verbose:
                    delta = msg.event.get("delta", {})
                    if delta.get("type") == "text_delta":
                        chunk = delta.get("text", "")
                        if chunk:
                            yield {"type": "partial", "text": chunk}

                elif isinstance(msg, ResultMessage):
                    new_session_id = msg.session_id
                    result_text = msg.result or ""
                    if new_session_id:
                        set_session_id(chat_id, thread_id, user_id, new_session_id)
                        sdk_session.session_id = new_session_id
                        logger.info("Session updated for user %d: %s", user_id, new_session_id)
                    ws_log.info("Result — session=%s, len=%d", new_session_id, len(result_text))
                    yield {"type": "result", "text": result_text, "session_id": new_session_id}

        except Exception as e:
            logger.exception("SDK streaming error")
            # Disconnect broken session so next message creates fresh one
            await sdk_session.disconnect()
            _sdk_sessions.pop(session_key, None)
            if result_text is None:
                yield {"type": "error", "text": f"Claude error: {e}"}
            return

        if result_text is None:
            logger.warning("No result message received from SDK")
            yield {"type": "error", "text": "Claude returned no result."}

    except Exception as e:
        logger.exception("Unexpected error in SDK stream_claude")
        yield {"type": "error", "text": f"Unexpected error: {e}"}
    finally:
        _remove_active_stream(chat_id, thread_id, user_id)


async def _stream_claude_subprocess(message: str, chat_id: int, thread_id: int, user_id: int,
                                     working_dir: str | None = None, verbose: bool = False):
    """Legacy subprocess-based streaming — fallback when SDK is unavailable."""
    cwd = working_dir or WORKING_DIR
    session_id = get_session_id(chat_id, thread_id, user_id)
    ws_log = get_workspace_logger(chat_id)
    ws_log.info("Claude invocation (subprocess) — user=%d, session=%s", user_id, session_id or "new")

    _add_active_stream(chat_id, thread_id, user_id)

    try:
        is_admin = ADMIN_USER_ID and user_id == ADMIN_USER_ID

        preamble = _build_preamble(is_admin, session_id)
        if preamble:
            message = preamble + message

        claude_bin = shutil.which("claude") or "/root/.local/bin/claude"
        logger.info("Using claude binary: %s (exists: %s)", claude_bin, os.path.isfile(claude_bin))
        cmd = [
            claude_bin,
            "-p", message,
            "--output-format", "stream-json",
            "--verbose",
            "--dangerously-skip-permissions",
            "--allowedTools", ALL_TOOLS,
        ]

        if verbose:
            cmd.append("--include-partial-messages")

        if session_id:
            cmd.extend(["--resume", session_id])

        if CLAUDE_MODEL:
            cmd.extend(["--model", CLAUDE_MODEL])

        logger.info(
            "Calling Claude (subprocess) for user %d (session: %s)",
            user_id,
            session_id or "new",
        )

        env = _build_env(is_admin, cwd, thread_id)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
            limit=10 * 1024 * 1024,
        )

        result_text = None
        new_session_id = None
        deadline = asyncio.get_event_loop().time() + CLAUDE_TIMEOUT

        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                proc.kill()
                await proc.communicate()
                logger.error("Claude CLI timed out after %ds for user %d", CLAUDE_TIMEOUT, user_id)
                yield {"type": "error", "text": "Claude took too long to respond. Try again or /new to start fresh."}
                return

            try:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=remaining)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                logger.error("Claude CLI timed out after %ds for user %d", CLAUDE_TIMEOUT, user_id)
                yield {"type": "error", "text": "Claude took too long to respond. Try again or /new to start fresh."}
                return

            if not line:
                break

            decoded = line.decode().strip()
            if not decoded:
                continue

            try:
                event = json.loads(decoded)
            except json.JSONDecodeError:
                logger.debug("Non-JSON line from Claude: %s", decoded[:200])
                continue

            event_type = event.get("type")

            if event_type == "assistant":
                msg = event.get("message", {})
                content = msg.get("content", [])
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tool_name = block.get("name", "")
                        tool_input = block.get("input", {})
                        ws_log.info("Tool: %s — %s", tool_name, _summarize_input(tool_input))
                        status = format_tool_status(tool_name, tool_input)
                        yield {"type": "tool_use", "status": status}
            elif event_type == "tool_result":
                yield {"type": "tool_result"}

            elif event_type == "stream_event" and verbose:
                delta = event.get("event", {}).get("delta", {})
                if delta.get("type") == "text_delta":
                    chunk = delta.get("text", "")
                    if chunk:
                        yield {"type": "partial", "text": chunk}

            elif event_type == "result":
                result_text = event.get("result", "")
                new_session_id = event.get("session_id")
                if new_session_id:
                    set_session_id(chat_id, thread_id, user_id, new_session_id)
                    logger.info("Session updated for user %d: %s", user_id, new_session_id)
                ws_log.info("Result — session=%s, len=%d", new_session_id, len(result_text or ""))
                yield {"type": "result", "text": result_text, "session_id": new_session_id}

        await proc.wait()

        if proc.returncode != 0:
            if proc.returncode < 0:
                sig = -proc.returncode
                logger.info("Claude CLI killed by signal %d (likely bot restart)", sig)
                return
            stderr_data = await proc.stderr.read()
            error_msg = stderr_data.decode().strip() if stderr_data else "Unknown error"
            logger.error("Claude CLI error (rc=%d): %s", proc.returncode, error_msg)
            ws_log.error("CLI error rc=%d: %s", proc.returncode, error_msg[:200])
            if result_text is None:
                yield {"type": "error", "text": f"Claude CLI error:\n{error_msg}"}
            return

        if result_text is None:
            logger.warning("No result event received from stream")
            yield {"type": "error", "text": "Claude returned no result."}

    except FileNotFoundError as e:
        logger.exception("FileNotFoundError in stream_claude: %s", e)
        yield {
            "type": "error",
            "text": "Error: Claude CLI not found. "
                    "Make sure 'claude' is installed and available in PATH.",
        }
    except Exception as e:
        logger.exception("Unexpected error streaming Claude")
        yield {"type": "error", "text": f"Unexpected error: {e}"}
    finally:
        _remove_active_stream(chat_id, thread_id, user_id)


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


def is_authorized(user_id: int) -> bool:
    """Check if a user is authorized to use the bot."""
    if not ALLOWED_USERS:
        logger.warning("ALLOWED_USERS is empty — no one is authorized!")
        return False
    return user_id in ALLOWED_USERS


# ---------------------------------------------------------------------------
# Telegram Handlers
# ---------------------------------------------------------------------------

renderer = TelegramRenderer()

# Per-user locks to prevent concurrent Claude calls for the same user
_user_locks: dict[int, asyncio.Lock] = {}

def _get_user_lock(user_id: int) -> asyncio.Lock:
    if user_id not in _user_locks:
        _user_locks[user_id] = asyncio.Lock()
    return _user_locks[user_id]


async def send_rendered(
    update: Update,
    text: str,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Render markdown to HTML and send, splitting if needed."""
    # Split markdown first, then render each chunk — avoids breaking HTML tags
    md_chunks = split_message(text)
    thread_id = get_thread_id(update)

    for md_chunk in md_chunks:
        chunk = renderer.render(md_chunk)
        try:
            await update.message.reply_text(
                chunk,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                message_thread_id=thread_id or None,
            )
        except Exception:
            logger.warning("HTML send failed for chunk, falling back to plain text")
            plain = re.sub(r"<[^>]+>", "", chunk)
            plain_chunks = split_message(plain)
            for pc in plain_chunks:
                await update.message.reply_text(
                    pc,
                    message_thread_id=thread_id or None,
                )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    user = update.effective_user
    if not is_authorized(user.id):
        await update.message.reply_text(
            f"Unauthorized. Your user ID is: {user.id}\n"
            "Add it to ALLOWED_USERS in .env to use this bot."
        )
        return

    # Built-in commands
    cmd_lines = [
        "/new — Start a new conversation",
        "/status — Show session info",
    ]
    # Dynamic commands from modules
    for name, desc in ALL_COMMANDS:
        cmd_lines.append(f"/{name} — {desc}")

    await update.message.reply_text(
        "OpenClaude is online.\n"
        "Send me a message and I'll route it to Claude.\n\n"
        "Commands:\n" + "\n".join(cmd_lines)
    )


async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /new command — clear session and start fresh."""
    user = update.effective_user
    if not is_authorized(user.id):
        return

    chat_id = update.effective_chat.id
    thread_id = get_thread_id(update)
    session_uid = user.id if update.effective_chat.type == "private" else 0

    # Disconnect SDK session if active
    sdk_key = _session_key(chat_id, thread_id, session_uid)
    sdk_session = _sdk_sessions.pop(sdk_key, None)
    if sdk_session:
        await sdk_session.disconnect()

    clear_session(chat_id, thread_id, session_uid)
    await update.message.reply_text(
        "Session cleared. Starting fresh.",
        message_thread_id=thread_id or None,
    )
    logger.info("Session cleared for user %d in chat %d thread %d", user.id, chat_id, thread_id)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /status command — show user ID and session info."""
    user = update.effective_user
    if not is_authorized(user.id):
        await update.message.reply_text(f"Your Telegram user ID: {user.id}")
        return

    chat_id = update.effective_chat.id
    thread_id = get_thread_id(update)
    session_uid = user.id if update.effective_chat.type == "private" else 0
    session_id = get_session_id(chat_id, thread_id, session_uid)
    sessions = load_sessions()
    key = _session_key(chat_id, thread_id, session_uid)
    user_data = sessions.get(key, {})

    status_lines = [
        f"<b>OpenClaude Status</b>",
        f"",
        f"<b>User ID:</b> <code>{user.id}</code>",
        f"<b>Username:</b> @{html.escape(user.username) if user.username else 'N/A'}",
        f"<b>Session:</b> <code>{session_id or 'None'}</code>",
    ]

    if updated := user_data.get("updated_at"):
        status_lines.append(f"<b>Last active:</b> {updated}")

    chat_dir = get_working_dir(chat_id)
    status_lines.extend([
        f"",
        f"<b>Working dir:</b> <code>{chat_dir}</code>",
        f"<b>Allowed tools:</b> {ALL_TOOLS}",
    ])

    await update.message.reply_text(
        "\n".join(status_lines),
        parse_mode=ParseMode.HTML,
        message_thread_id=thread_id or None,
    )


# ---------------------------------------------------------------------------
# Message Batching — combine rapid-fire messages into one Claude turn
# ---------------------------------------------------------------------------

# Batch window: messages arriving within this many seconds are combined
BATCH_WINDOW = 1.5

# Per-session batch state
_batch_buffers: dict[str, list[str]] = {}
_batch_timers: dict[str, asyncio.TimerHandle] = {}
_batch_updates: dict[str, tuple[Update, ContextTypes.DEFAULT_TYPE]] = {}
_batch_meta: dict[str, tuple[int, int, int]] = {}  # key → (chat_id, thread_id, user_id)


async def _queue_message(update: Update, context: ContextTypes.DEFAULT_TYPE,
                         chat_id: int, thread_id: int, user_id: int,
                         claude_message: str) -> None:
    """Add a message to the batch buffer. After BATCH_WINDOW seconds of quiet, flush all."""
    session_user_id = user_id if update.effective_chat.type == "private" else 0
    key = _session_key(chat_id, thread_id, session_user_id)

    _batch_buffers.setdefault(key, []).append(claude_message)
    # Always keep the latest update so we reply to the last message
    _batch_updates[key] = (update, context)
    _batch_meta[key] = (chat_id, thread_id, user_id)

    # Cancel previous timer, start a new one
    if key in _batch_timers:
        _batch_timers[key].cancel()

    loop = asyncio.get_event_loop()
    _batch_timers[key] = loop.call_later(
        BATCH_WINDOW,
        lambda k=key: asyncio.ensure_future(_flush_batch(k)),
    )


async def _flush_batch(key: str) -> None:
    """Flush the batch buffer — combine messages and send to Claude."""
    messages = _batch_buffers.pop(key, [])
    update_ctx = _batch_updates.pop(key, None)
    meta = _batch_meta.pop(key, None)
    _batch_timers.pop(key, None)

    if not messages or not update_ctx or not meta:
        return

    update, context = update_ctx
    chat_id, thread_id, user_id = meta

    if len(messages) == 1:
        combined = messages[0]
    else:
        combined = "\n\n".join(messages)

    await _run_with_streaming(update, context, chat_id, thread_id, user_id, combined)


async def _run_with_streaming(update: Update, context: ContextTypes.DEFAULT_TYPE,
                              chat_id: int, thread_id: int, user_id: int,
                              claude_message: str) -> None:
    """Stream Claude output, show tool progress via an editable status message, then send final response."""
    # In group chats, share a single session across all users so the
    # conversation stays coherent.  Private chats keep per-user sessions.
    session_user_id = user_id if update.effective_chat.type == "private" else 0
    tg_thread_id = thread_id or None
    streaming = get_streaming(chat_id, thread_id)
    show_tools = get_verbose(chat_id, thread_id)
    status_msg = None         # The editable Telegram status message
    finished_lines: list[str] = []  # Lines with checkmarks for completed tools
    current_active: str = ""  # The currently-active tool line
    last_edit_time: float = 0

    # Verbose mode: live response message
    live_msg = None           # Separate message for live text
    live_text = ""            # Accumulated partial text
    last_live_edit: float = 0
    LIVE_EDIT_INTERVAL = 2.0  # Telegram rate limit: ~20 edits/min

    async def _update_status(new_active: str = "") -> None:
        """Edit the status message with current tool progress."""
        nonlocal status_msg, current_active, last_edit_time
        current_active = new_active
        lines = list(finished_lines)
        if current_active:
            lines.append(current_active)
        if not lines:
            return

        text = "\n".join(lines)

        now = asyncio.get_event_loop().time()
        if status_msg and (now - last_edit_time) < STATUS_EDIT_INTERVAL:
            return  # Rate-limit edits

        try:
            if status_msg is None:
                status_msg = await update.message.reply_text(
                    text,
                    message_thread_id=tg_thread_id,
                )
            else:
                await status_msg.edit_text(text)
            last_edit_time = asyncio.get_event_loop().time()
        except Exception:
            # Silently ignore edit failures (e.g., message unchanged)
            pass

    async def _update_live(text: str) -> None:
        """Create or edit the live response message."""
        nonlocal live_msg, last_live_edit

        now = asyncio.get_event_loop().time()
        if live_msg and (now - last_live_edit) < LIVE_EDIT_INTERVAL:
            return

        # Truncate for Telegram limit, add typing indicator
        display = text[:TELEGRAM_MAX_LENGTH - 20] + " \u270d\ufe0f" if text else ""
        if not display:
            return

        try:
            if live_msg is None:
                live_msg = await update.message.reply_text(
                    display,
                    message_thread_id=tg_thread_id,
                )
            else:
                await live_msg.edit_text(display)
            last_live_edit = asyncio.get_event_loop().time()
        except Exception:
            pass

    response_text = None
    chat_working_dir = get_working_dir(chat_id)
    in_tool = False  # Track whether we're inside a tool call

    async with _get_user_lock(session_user_id):
        async for event in stream_claude(claude_message, chat_id, thread_id, session_user_id,
                                         working_dir=chat_working_dir, verbose=streaming):
            etype = event.get("type")

            if etype == "tool_use":
                in_tool = True
                live_text = ""  # Reset live text when entering a tool call
                if show_tools:
                    # Mark previous active tool as finished
                    if current_active:
                        finished_lines.append(_finished_line(current_active))
                    await _update_status(event["status"])

            elif etype == "tool_result":
                in_tool = False
                if show_tools:
                    # Mark current tool as finished
                    if current_active:
                        finished_lines.append(_finished_line(current_active))
                        await _update_status("")

            elif etype == "partial":
                if not in_tool:
                    live_text += event["text"]
                    await _update_live(live_text)

            elif etype == "result":
                response_text = event.get("text", "")

            elif etype == "error":
                response_text = event.get("text", "An error occurred.")

    # Clean up status message
    if status_msg:
        try:
            await status_msg.delete()
        except Exception:
            pass

    # In streaming mode, edit the live message with the final rendered response
    if response_text is None:
        response_text = "Claude processed the request but returned no text output."

    if live_msg and streaming:
        # Replace the live message with the final rendered text
        try:
            rendered = renderer.render(response_text)
            if len(rendered) <= TELEGRAM_MAX_LENGTH:
                await live_msg.edit_text(
                    rendered,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
            else:
                # Too long for one message — delete live msg, send split
                await live_msg.delete()
                await send_rendered(update, response_text, context)
        except Exception:
            # Fallback: delete live msg, send fresh
            try:
                await live_msg.delete()
            except Exception:
                pass
            await send_rendered(update, response_text, context)
    else:
        await send_rendered(update, response_text, context)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming text messages — route to Claude."""
    user = update.effective_user
    if not is_authorized(user.id):
        return

    if not should_respond(update):
        return

    message_text = update.message.text
    if not message_text:
        return

    # Strip @bot_username from the text before sending to Claude
    message_text = strip_bot_mention(message_text)
    if not message_text:
        return

    chat_id = update.effective_chat.id
    thread_id = get_thread_id(update)

    logger.info(
        "Message from %s (%d) in chat %d thread %d, length=%d",
        user.username or user.first_name,
        user.id,
        chat_id,
        thread_id,
        len(message_text),
    )
    get_workspace_logger(chat_id).info(
        "Message from user %d (%s), length=%d",
        user.id, user.username or user.first_name, len(message_text),
    )

    await _queue_message(update, context, chat_id, thread_id, user.id, message_text)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming voice messages and audio — transcribe and route to Claude."""
    user = update.effective_user
    if not is_authorized(user.id):
        return

    if not should_respond(update):
        return

    voice = update.message.voice or update.message.audio
    if not voice:
        return

    chat_id = update.effective_chat.id
    thread_id = get_thread_id(update)

    logger.info(
        "Voice/audio from %s (%d) in chat %d thread %d, duration=%s",
        user.username or user.first_name,
        user.id,
        chat_id,
        thread_id,
        getattr(voice, "duration", "?"),
    )
    get_workspace_logger(chat_id).info(
        "Voice from user %d (%s), duration=%s",
        user.id, user.username or user.first_name, getattr(voice, "duration", "?"),
    )

    workspace = ensure_workspace(chat_id)
    voice_dir = workspace / "uploads" / f"t{thread_id}" / "voice"
    voice_dir.mkdir(parents=True, exist_ok=True)
    ogg_path = voice_dir / f"{voice.file_id}.ogg"

    file = await context.bot.get_file(voice.file_id)
    await file.download_to_drive(ogg_path)

    text = await transcribe(ogg_path)
    caption = update.message.caption or ""
    claude_msg = f'[Voice message transcription]: "{text}"'
    if caption:
        claude_msg += f' User also wrote: "{caption}"'

    await _queue_message(update, context, chat_id, thread_id, user.id, claude_msg)


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming documents/files — download and tell Claude the path."""
    user = update.effective_user
    if not is_authorized(user.id):
        return

    if not should_respond(update):
        return

    doc = update.message.document
    if not doc:
        return

    chat_id = update.effective_chat.id
    thread_id = get_thread_id(update)

    logger.info(
        "Document from %s (%d) in chat %d thread %d: %s (%s bytes)",
        user.username or user.first_name,
        user.id,
        chat_id,
        thread_id,
        doc.file_name,
        doc.file_size,
    )
    get_workspace_logger(chat_id).info(
        "Document from user %d: %s (%s bytes)",
        user.id, doc.file_name, doc.file_size,
    )

    workspace = ensure_workspace(chat_id)
    today = datetime.now().strftime("%Y-%m-%d")
    dest_dir = workspace / "uploads" / f"t{thread_id}" / today
    dest_dir.mkdir(parents=True, exist_ok=True)
    # Sanitize filename: strip path components to prevent path traversal
    safe_name = Path(doc.file_name).name if doc.file_name else f"file_{doc.file_id}"
    dest = dest_dir / safe_name

    file = await context.bot.get_file(doc.file_id)
    await file.download_to_drive(dest)

    caption = update.message.caption or ""
    claude_msg = f"[File received: {dest.relative_to(workspace)}]"
    if caption:
        claude_msg += f' User says: "{caption}"'

    await _queue_message(update, context, chat_id, thread_id, user.id, claude_msg)


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming video messages — download and tell Claude the path."""
    user = update.effective_user
    if not is_authorized(user.id):
        return

    if not should_respond(update):
        return

    video = update.message.video
    if not video:
        return

    chat_id = update.effective_chat.id
    thread_id = get_thread_id(update)

    logger.info(
        "Video from %s (%d) in chat %d thread %d: %s (%s bytes)",
        user.username or user.first_name,
        user.id,
        chat_id,
        thread_id,
        video.file_name or video.file_id,
        video.file_size,
    )
    get_workspace_logger(chat_id).info(
        "Video from user %d: %s (%s bytes)",
        user.id, video.file_name or video.file_id, video.file_size,
    )

    workspace = ensure_workspace(chat_id)
    today = datetime.now().strftime("%Y-%m-%d")
    dest_dir = workspace / "uploads" / f"t{thread_id}" / today
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(video.file_name).name if video.file_name else f"video_{video.file_id}.mp4"
    dest = dest_dir / safe_name

    file = await context.bot.get_file(video.file_id)
    await file.download_to_drive(dest)

    caption = update.message.caption or ""
    claude_msg = f"[Video received: {dest.relative_to(workspace)}]"
    if caption:
        claude_msg += f' User says: "{caption}"'

    await _queue_message(update, context, chat_id, thread_id, user.id, claude_msg)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming photos — download largest size and tell Claude the path."""
    user = update.effective_user
    if not is_authorized(user.id):
        return

    if not should_respond(update):
        return

    photos = update.message.photo
    if not photos:
        return

    # Telegram sends multiple sizes; last is the largest
    photo = photos[-1]

    chat_id = update.effective_chat.id
    thread_id = get_thread_id(update)

    logger.info(
        "Photo from %s (%d) in chat %d thread %d, size=%dx%d",
        user.username or user.first_name,
        user.id,
        chat_id,
        thread_id,
        photo.width,
        photo.height,
    )
    get_workspace_logger(chat_id).info(
        "Photo from user %d, size=%dx%d",
        user.id, photo.width, photo.height,
    )

    workspace = ensure_workspace(chat_id)
    today = datetime.now().strftime("%Y-%m-%d")
    dest_dir = workspace / "uploads" / f"t{thread_id}" / today
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"photo_{photo.file_unique_id}.jpg"

    file = await context.bot.get_file(photo.file_id)
    await file.download_to_drive(dest)

    caption = update.message.caption or ""
    claude_msg = f"[Photo received: {dest.relative_to(workspace)}]"
    if caption:
        claude_msg += f' User says: "{caption}"'

    await _queue_message(update, context, chat_id, thread_id, user.id, claude_msg)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """Start the bot."""
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set. Check your .env file.")
        sys.exit(1)

    if not ALLOWED_USERS:
        logger.warning(
            "ALLOWED_USERS is empty. No one will be able to use the bot. "
            "Set ALLOWED_USERS in .env with your Telegram user ID."
        )
        infra_logger.warning("ALLOWED_USERS is empty — no one is authorized")

    logger.info("Starting OpenClaude Telegram bot...")
    logger.info("Allowed users: %s", ALLOWED_USERS)
    logger.info("Working directory: %s", WORKING_DIR)
    logger.info("Session file: %s", SESSION_FILE)
    infra_logger.info("Bot starting — users=%s, workdir=%s", ALLOWED_USERS, WORKING_DIR)

    atexit.register(lambda: infra_logger.info("Bot process exiting"))

    async def post_init(application: Application) -> None:
        """Fetch bot info at startup and resume interrupted generations."""
        global BOT_USERNAME
        bot = application.bot
        me = await bot.get_me()
        BOT_USERNAME = me.username or ""
        logger.info("Bot username: @%s", BOT_USERNAME)
        infra_logger.info("Bot username: @%s", BOT_USERNAME)

        # Register commands in Telegram's menu (refreshes the cached list)
        from telegram import BotCommand
        bot_commands = [
            BotCommand("start", "Show welcome message"),
            BotCommand("new", "Start a new conversation"),
            BotCommand("status", "Show session info"),
        ]
        for name, desc in ALL_COMMANDS:
            bot_commands.append(BotCommand(name, desc))
        await bot.set_my_commands(bot_commands)
        logger.info("Registered %d bot commands with Telegram", len(bot_commands))

        # Start SDK idle session cleanup task
        if HAS_SDK:
            asyncio.create_task(_cleanup_idle_sessions())
            logger.info("SDK idle session cleanup task started")

        # Collect interrupted chats from two sources:
        # 1. .restart-state.json — snapshot from restart.sh (controlled restart)
        # 2. .active-streams.json — surviving entries from crash (finally blocks didn't run)
        interrupted: dict[str, dict] = {}

        for state_file in (RESTART_STATE_FILE, ACTIVE_STREAMS_FILE):
            if not state_file.exists():
                continue
            try:
                data = json.loads(state_file.read_text())
                interrupted.update(data)
            except (json.JSONDecodeError, OSError):
                pass
            finally:
                state_file.unlink(missing_ok=True)

        if not interrupted:
            return

        infra_logger.info("Resuming %d interrupted generation(s)", len(interrupted))

        async def _resume_chat(entry: dict) -> None:
            cid = entry["chat_id"]
            tid = entry["thread_id"]
            uid = entry["user_id"]
            try:
                session_id = get_session_id(cid, tid, uid)
                if not session_id:
                    infra_logger.warning(
                        "No session for chat=%d thread=%d user=%d, skipping resume",
                        cid, tid, uid,
                    )
                    return
                resume_msg = (
                    "[System: The bot just restarted. Continue where you left off "
                    "and deliver the result to the user.]"
                )
                chat_working_dir = get_working_dir(cid)
                result_text = None
                async for event in stream_claude(resume_msg, cid, tid, uid,
                                                 working_dir=chat_working_dir):
                    if event.get("type") == "result":
                        result_text = event.get("text", "")
                    elif event.get("type") == "error":
                        result_text = event.get("text", "")
                if result_text:
                    md_chunks = split_message(result_text)
                    tg_thread_id = tid or None
                    for md_chunk in md_chunks:
                        rendered = renderer.render(md_chunk)
                        try:
                            await bot.send_message(
                                chat_id=cid,
                                text=rendered,
                                parse_mode=ParseMode.HTML,
                                disable_web_page_preview=True,
                                message_thread_id=tg_thread_id,
                            )
                        except Exception:
                            plain = re.sub(r"<[^>]+>", "", rendered)
                            for pc in split_message(plain):
                                await bot.send_message(
                                    chat_id=cid,
                                    text=pc,
                                    message_thread_id=tg_thread_id,
                                )
                infra_logger.info("Resumed chat=%d thread=%d user=%d", cid, tid, uid)
            except Exception as e:
                infra_logger.error(
                    "Failed to resume chat=%d thread=%d user=%d: %s", cid, tid, uid, e
                )

        await asyncio.gather(*[_resume_chat(e) for e in interrupted.values()])
        infra_logger.info("Restart recovery complete")

    async def post_shutdown(application: Application) -> None:
        """Clean up SDK sessions on shutdown."""
        if HAS_SDK:
            await _shutdown_sdk_sessions()
            infra_logger.info("SDK sessions shut down")

    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # Initialize command modules with references to our globals
    _init_commands(globals())

    # Register handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("new", cmd_new))
    app.add_handler(CommandHandler("status", cmd_status))
    register_all(app)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.VIDEO, handle_video))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    # Start polling
    logger.info("Bot is running. Press Ctrl+C to stop.")
    infra_logger.info("Bot running")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
