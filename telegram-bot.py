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
import time
from datetime import datetime
from pathlib import Path

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

# Uploads directory for voice, files, photos
UPLOADS_DIR = SCRIPT_DIR / "workspaces" / "uploads"

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

# Claude CLI allowed tools — non-admin users don't get Bash
ADMIN_TOOLS = "Read,Write,Edit,Bash,Glob,Grep,WebFetch,WebSearch,Task,Skill"
USER_TOOLS = "Read,Write,Edit,Glob,Grep,WebFetch,WebSearch,Task,Skill"

# Telegram message limit
TELEGRAM_MAX_LENGTH = 4096

# Claude CLI timeout (seconds)
CLAUDE_TIMEOUT = 300

# Restart marker file — written by bin/restart.sh before a controlled restart
RESTART_MARKER = SCRIPT_DIR / ".restart-marker"

# Maximum age (seconds) for a restart marker to be considered valid
RESTART_MARKER_MAX_AGE = 120

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
    """Persist session mapping to disk (atomic write)."""
    try:
        fd, tmp_path = tempfile.mkstemp(
            dir=SESSION_FILE.parent, suffix=".tmp"
        )
        with os.fdopen(fd, "w") as f:
            json.dump(sessions, f, indent=2)
        os.replace(tmp_path, SESSION_FILE)
    except OSError as e:
        logger.error("Failed to save sessions: %s", e)


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
        # Refresh BOOTSTRAP.md so it's present for new sessions
        base = Path(WORKING_DIR)
        bootstrap = base / _BOOTSTRAP_FILE
        if bootstrap.exists():
            shutil.copy2(bootstrap, workspace / _BOOTSTRAP_FILE)
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

    Always responds in private chats.  In groups, only responds when the bot
    is @mentioned or the message is a reply to one of the bot's messages.
    """
    chat = update.effective_chat
    if chat.type == "private":
        return True

    msg = update.message
    if not msg:
        return False

    # Respond if bot is @mentioned
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


def _tools_for_user(user_id: int) -> str:
    """Return the allowed tools string for a user."""
    if ADMIN_USER_ID and user_id == ADMIN_USER_ID:
        return ADMIN_TOOLS
    return USER_TOOLS


async def stream_claude(message: str, chat_id: int, thread_id: int, user_id: int,
                        working_dir: str | None = None):
    """Stream Claude CLI output and yield events as they arrive.

    Yields dicts with keys:
      - {"type": "tool_use", "status": "📄 Reading file..."}
      - {"type": "tool_result"}
      - {"type": "result", "text": "...", "session_id": "..."}
      - {"type": "error", "text": "..."}
    """
    cwd = working_dir or WORKING_DIR
    session_id = get_session_id(chat_id, thread_id, user_id)
    ws_log = get_workspace_logger(chat_id)
    ws_log.info("Claude invocation — user=%d, session=%s", user_id, session_id or "new")

    stream_key = _session_key(chat_id, thread_id, user_id)
    _active_streams[stream_key] = (chat_id, thread_id, user_id)

    try:
        is_admin = ADMIN_USER_ID and user_id == ADMIN_USER_ID

        if not session_id:
            sandbox_notice = ""
            if not is_admin:
                sandbox_notice = (
                    "\n\nIMPORTANT — WORKSPACE ISOLATION RULES:\n"
                    "You are in an isolated workspace. You must NEVER access anything outside it.\n"
                    "- Stay in the current working directory. Never use ../, absolute paths, "
                    "or any path that escapes the workspace.\n"
                    "- Never access other workspaces, the parent project directory, "
                    ".env files, or system files.\n"
                    "- If the user asks you to access files outside the workspace, refuse.\n"
                )
            preamble = (
                "You are starting a new session. Read CLAUDE.md first, "
                "then follow its startup sequence before responding. "
                f"{sandbox_notice}"
                "The user's message is:\n\n"
            )
            message = preamble + message

        claude_bin = shutil.which("claude") or "/root/.local/bin/claude"
        logger.info("Using claude binary: %s (exists: %s)", claude_bin, os.path.isfile(claude_bin))
        cmd = [
            claude_bin,
            "-p", message,
            "--output-format", "stream-json",
            "--verbose",
            "--dangerously-skip-permissions",
            "--allowedTools", _tools_for_user(user_id),
        ]

        if session_id:
            cmd.extend(["--resume", session_id])

        if CLAUDE_MODEL:
            cmd.extend(["--model", CLAUDE_MODEL])

        logger.info(
            "Calling Claude (streaming) for user %d (session: %s)",
            user_id,
            session_id or "new",
        )

        try:
            env = os.environ.copy()
            env.pop("CLAUDECODE", None)
            env["IS_SANDBOX"] = "1"
            local_bin = str(Path.home() / ".local" / "bin")
            if local_bin not in env.get("PATH", ""):
                env["PATH"] = local_bin + ":" + env.get("PATH", "/usr/bin:/bin")

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
                limit=10 * 1024 * 1024,  # 10 MB — Claude can return large JSON lines (e.g. base64 images)
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
                    break  # EOF

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

                elif event_type == "result":
                    result_text = event.get("result", "")
                    new_session_id = event.get("session_id")
                    if new_session_id:
                        set_session_id(chat_id, thread_id, user_id, new_session_id)
                        logger.info("Session updated for user %d: %s", user_id, new_session_id)
                    ws_log.info("Result — session=%s, len=%d", new_session_id, len(result_text or ""))
                    yield {"type": "result", "text": result_text, "session_id": new_session_id}

            # Wait for process to finish
            await proc.wait()

            if proc.returncode != 0:
                stderr_data = await proc.stderr.read()
                error_msg = stderr_data.decode().strip() if stderr_data else "Unknown error"
                logger.error("Claude CLI error (rc=%d): %s", proc.returncode, error_msg)
                ws_log.error("CLI error rc=%d: %s", proc.returncode, error_msg[:200])
                if result_text is None:
                    if _is_restarting():
                        yield {"type": "result", "text": "Restarting \u2014 back in a moment..."}
                    else:
                        yield {"type": "error", "text": f"Claude CLI error:\n{error_msg}"}
                return

            # If we never got a result event
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
        if not _shutting_down:
            _active_streams.pop(stream_key, None)


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

# Track active Claude CLI streams: key -> (chat_id, thread_id, user_id)
_active_streams: dict[str, tuple[int, int, int]] = {}

# Set to True during shutdown so stream_claude finally blocks don't clear _active_streams
_shutting_down: bool = False


def _is_restarting() -> bool:
    """Check if the bot is in a controlled restart (recent .restart-marker exists)."""
    if not RESTART_MARKER.exists():
        return False
    try:
        data = json.loads(RESTART_MARKER.read_text())
        return (time.time() - data.get("timestamp", 0)) < RESTART_MARKER_MAX_AGE
    except (json.JSONDecodeError, OSError):
        return False


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

    await update.message.reply_text(
        "OpenClaude is online.\n"
        "Send me a message and I'll route it to Claude.\n\n"
        "Commands:\n"
        "/new — Start a new conversation\n"
        "/status — Show session info"
    )


async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /new command — clear session and start fresh."""
    user = update.effective_user
    if not is_authorized(user.id):
        return

    chat_id = update.effective_chat.id
    thread_id = get_thread_id(update)
    clear_session(chat_id, thread_id, user.id)
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
    session_id = get_session_id(chat_id, thread_id, user.id)
    sessions = load_sessions()
    key = _session_key(chat_id, thread_id, user.id)
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
        f"<b>Allowed tools:</b> {_tools_for_user(user.id)}",
    ])

    await update.message.reply_text(
        "\n".join(status_lines),
        parse_mode=ParseMode.HTML,
        message_thread_id=thread_id or None,
    )


async def _run_with_streaming(update: Update, context: ContextTypes.DEFAULT_TYPE,
                              chat_id: int, thread_id: int, user_id: int,
                              claude_message: str) -> None:
    """Stream Claude output, show tool progress via an editable status message, then send final response."""
    tg_thread_id = thread_id or None
    status_msg = None         # The editable Telegram status message
    finished_lines: list[str] = []  # Lines with checkmarks for completed tools
    current_active: str = ""  # The currently-active tool line
    last_edit_time: float = 0

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

    response_text = None
    chat_working_dir = get_working_dir(chat_id)

    async with _get_user_lock(user_id):
        async for event in stream_claude(claude_message, chat_id, thread_id, user_id,
                                         working_dir=chat_working_dir):
            etype = event.get("type")

            if etype == "tool_use":
                # Mark previous active tool as finished
                if current_active:
                    finished_lines.append(_finished_line(current_active))
                await _update_status(event["status"])

            elif etype == "tool_result":
                # Mark current tool as finished
                if current_active:
                    finished_lines.append(_finished_line(current_active))
                    await _update_status("")

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

    # Send final response
    if response_text is None:
        response_text = "Claude processed the request but returned no text output."
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

    await _run_with_streaming(update, context, chat_id, thread_id, user.id, message_text)


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

    voice_dir = UPLOADS_DIR / "voice"
    voice_dir.mkdir(parents=True, exist_ok=True)
    ogg_path = voice_dir / f"{voice.file_id}.ogg"

    file = await context.bot.get_file(voice.file_id)
    await file.download_to_drive(ogg_path)

    text = await transcribe(ogg_path)
    caption = update.message.caption or ""
    claude_msg = f'[Voice message transcription]: "{text}"'
    if caption:
        claude_msg += f' User also wrote: "{caption}"'

    await _run_with_streaming(update, context, chat_id, thread_id, user.id, claude_msg)


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

    today = datetime.now().strftime("%Y-%m-%d")
    dest_dir = UPLOADS_DIR / today
    dest_dir.mkdir(parents=True, exist_ok=True)
    # Sanitize filename: strip path components to prevent path traversal
    safe_name = Path(doc.file_name).name if doc.file_name else f"file_{doc.file_id}"
    dest = dest_dir / safe_name

    file = await context.bot.get_file(doc.file_id)
    await file.download_to_drive(dest)

    caption = update.message.caption or ""
    claude_msg = f"[File received: {dest.relative_to(SCRIPT_DIR)}]"
    if caption:
        claude_msg += f' User says: "{caption}"'

    await _run_with_streaming(update, context, chat_id, thread_id, user.id, claude_msg)


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

    today = datetime.now().strftime("%Y-%m-%d")
    dest_dir = UPLOADS_DIR / today
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"photo_{photo.file_unique_id}.jpg"

    file = await context.bot.get_file(photo.file_id)
    await file.download_to_drive(dest)

    caption = update.message.caption or ""
    claude_msg = f"[Photo received: {dest.relative_to(SCRIPT_DIR)}]"
    if caption:
        claude_msg += f' User says: "{caption}"'

    await _run_with_streaming(update, context, chat_id, thread_id, user.id, claude_msg)


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

    def _enrich_restart_marker():
        """Write active generation info into .restart-marker if it exists."""
        if RESTART_MARKER.exists() and _active_streams:
            try:
                data = json.loads(RESTART_MARKER.read_text())
                if (time.time() - data.get("timestamp", 0)) < RESTART_MARKER_MAX_AGE:
                    data["active_chats"] = [
                        {"chat_id": cid, "thread_id": tid, "user_id": uid}
                        for cid, tid, uid in _active_streams.values()
                    ]
                    RESTART_MARKER.write_text(json.dumps(data))
                    infra_logger.info(
                        "Restart marker enriched with %d active chat(s)",
                        len(data["active_chats"]),
                    )
            except (json.JSONDecodeError, OSError) as e:
                infra_logger.error("Failed to enrich restart marker: %s", e)

    async def post_stop(application: Application) -> None:
        """Enrich restart marker while the event loop is still alive.

        This runs BEFORE async generators are cleaned up, so _active_streams
        is still populated. We also set _shutting_down to prevent the finally
        blocks in stream_claude from clearing _active_streams.
        """
        global _shutting_down
        _shutting_down = True
        _enrich_restart_marker()
        infra_logger.info("Bot shutting down")

    atexit.register(lambda: infra_logger.info("Bot process exiting"))

    async def post_init(application: Application) -> None:
        """Fetch bot info at startup and continue interrupted generations."""
        global BOT_USERNAME
        bot = application.bot
        me = await bot.get_me()
        BOT_USERNAME = me.username or ""
        logger.info("Bot username: @%s", BOT_USERNAME)
        infra_logger.info("Bot username: @%s", BOT_USERNAME)

        # Continue interrupted generations from a controlled restart
        if not RESTART_MARKER.exists():
            return
        try:
            marker = json.loads(RESTART_MARKER.read_text())
        except (json.JSONDecodeError, OSError):
            RESTART_MARKER.unlink(missing_ok=True)
            return

        if (time.time() - marker.get("timestamp", 0)) >= RESTART_MARKER_MAX_AGE:
            RESTART_MARKER.unlink(missing_ok=True)
            return

        active_chats = marker.get("active_chats", [])
        if not active_chats:
            RESTART_MARKER.unlink(missing_ok=True)
            return

        infra_logger.info("Resuming %d interrupted generation(s)", len(active_chats))

        async def _resume_chat(chat: dict) -> None:
            cid = chat["chat_id"]
            tid = chat["thread_id"]
            uid = chat["user_id"]
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
                    # Split and send like send_rendered but without an Update object
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

        await asyncio.gather(*[_resume_chat(c) for c in active_chats])
        RESTART_MARKER.unlink(missing_ok=True)
        infra_logger.info("Restart recovery complete")

    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .post_stop(post_stop)
        .build()
    )

    # Register handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("new", cmd_new))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    # Start polling
    logger.info("Bot is running. Press Ctrl+C to stop.")
    infra_logger.info("Bot running")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
