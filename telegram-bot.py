#!/usr/bin/env python3
"""
OpenClaude Telegram Bot — A personal AI assistant powered by Claude Code.

Uses python-telegram-bot v21+ async API with Claude CLI as the backend.
Sessions are persisted to ~/.openclaude-sessions.json for conversation continuity.
"""

import asyncio
import html
import json
import logging
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ParseMode, ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Load .env from the script's directory
SCRIPT_DIR = Path(__file__).resolve().parent
load_dotenv(SCRIPT_DIR / ".env")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ALLOWED_USERS_RAW = os.getenv("ALLOWED_USERS", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "")
WORKING_DIR = os.getenv("WORKING_DIR", str(SCRIPT_DIR))

# Parse allowed users
ALLOWED_USERS: set[int] = set()
if ALLOWED_USERS_RAW.strip():
    for uid in ALLOWED_USERS_RAW.split(","):
        uid = uid.strip()
        if uid.isdigit():
            ALLOWED_USERS.add(int(uid))

# Session file
SESSION_FILE = Path.home() / ".openclaude-sessions.json"

# Claude CLI allowed tools
ALLOWED_TOOLS = "Read,Write,Edit,Bash,Glob,Grep,WebFetch,WebSearch,Task,Skill"

# Telegram message limit
TELEGRAM_MAX_LENGTH = 4096

# Claude CLI timeout (seconds)
CLAUDE_TIMEOUT = 300

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


def get_session_id(user_id: int) -> str | None:
    """Get the Claude session ID for a Telegram user."""
    sessions = load_sessions()
    return sessions.get(str(user_id), {}).get("session_id")


def set_session_id(user_id: int, session_id: str) -> None:
    """Store a Claude session ID for a Telegram user."""
    sessions = load_sessions()
    sessions.setdefault(str(user_id), {})["session_id"] = session_id
    sessions[str(user_id)]["updated_at"] = datetime.now().isoformat()
    save_sessions(sessions)


def clear_session(user_id: int) -> None:
    """Clear the session for a user, starting fresh."""
    sessions = load_sessions()
    if str(user_id) in sessions:
        del sessions[str(user_id)]
        save_sessions(sessions)


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
# Claude CLI Integration
# ---------------------------------------------------------------------------


async def call_claude(message: str, user_id: int) -> str:
    """Call the Claude CLI with a message and return the response text."""
    session_id = get_session_id(user_id)

    # On first message in a session, prepend instructions to read prompt files
    if not session_id:
        preamble = (
            "You are starting a new session. Read CLAUDE.md first, "
            "then follow its startup sequence before responding. "
            "The user's message is:\n\n"
        )
        message = preamble + message

    cmd = [
        "claude",
        "-p", message,
        "--output-format", "json",
        "--allowedTools", ALLOWED_TOOLS,
    ]

    if session_id:
        cmd.extend(["--resume", session_id])

    if CLAUDE_MODEL:
        cmd.extend(["--model", CLAUDE_MODEL])

    logger.info(
        "Calling Claude for user %d (session: %s)",
        user_id,
        session_id or "new",
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=WORKING_DIR,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=CLAUDE_TIMEOUT
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            logger.error("Claude CLI timed out after %ds for user %d", CLAUDE_TIMEOUT, user_id)
            return "Claude took too long to respond. Try again or /new to start fresh."

        if proc.returncode != 0:
            error_msg = stderr.decode().strip() if stderr else "Unknown error"
            logger.error("Claude CLI error (rc=%d): %s", proc.returncode, error_msg)
            return f"Claude CLI error:\n{error_msg}"

        raw = stdout.decode().strip()
        if not raw:
            return "Claude returned an empty response."

        # Parse JSON output
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # If it's not valid JSON, return raw text
            logger.warning("Claude returned non-JSON output")
            return raw

        # Extract session ID for continuity
        new_session_id = data.get("session_id")
        if new_session_id:
            set_session_id(user_id, new_session_id)
            logger.info("Session updated for user %d: %s", user_id, new_session_id)

        # Extract the response text
        result_text = data.get("result", "")

        # If result is empty, try to find text in other fields
        if not result_text:
            # Check for content blocks
            if "content" in data:
                parts = []
                for block in data["content"]:
                    if isinstance(block, dict) and block.get("type") == "text":
                        parts.append(block["text"])
                    elif isinstance(block, str):
                        parts.append(block)
                result_text = "\n".join(parts)

        if not result_text:
            result_text = "Claude processed the request but returned no text output."

        return result_text

    except FileNotFoundError:
        logger.error("Claude CLI not found. Is 'claude' installed and in PATH?")
        return (
            "Error: Claude CLI not found. "
            "Make sure 'claude' is installed and available in PATH."
        )
    except Exception as e:
        logger.exception("Unexpected error calling Claude")
        return f"Unexpected error: {e}"


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
    rendered = renderer.render(text)
    chunks = split_message(rendered)

    for chunk in chunks:
        try:
            await update.message.reply_text(
                chunk,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except Exception:
            logger.warning("HTML parse failed for chunk, falling back to plain text")
            plain = re.sub(r"<[^>]+>", "", chunk)
            await update.message.reply_text(plain)


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

    clear_session(user.id)
    await update.message.reply_text("Session cleared. Starting fresh.")
    logger.info("Session cleared for user %d", user.id)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /status command — show user ID and session info."""
    user = update.effective_user
    if not is_authorized(user.id):
        await update.message.reply_text(f"Your Telegram user ID: {user.id}")
        return

    session_id = get_session_id(user.id)
    sessions = load_sessions()
    user_data = sessions.get(str(user.id), {})

    status_lines = [
        f"<b>OpenClaude Status</b>",
        f"",
        f"<b>User ID:</b> <code>{user.id}</code>",
        f"<b>Username:</b> @{html.escape(user.username) if user.username else 'N/A'}",
        f"<b>Session:</b> <code>{session_id or 'None'}</code>",
    ]

    if updated := user_data.get("updated_at"):
        status_lines.append(f"<b>Last active:</b> {updated}")

    status_lines.extend([
        f"",
        f"<b>Working dir:</b> <code>{WORKING_DIR}</code>",
        f"<b>Allowed tools:</b> {ALLOWED_TOOLS}",
    ])

    await update.message.reply_text(
        "\n".join(status_lines),
        parse_mode=ParseMode.HTML,
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming text messages — route to Claude."""
    user = update.effective_user
    if not is_authorized(user.id):
        return

    message_text = update.message.text
    if not message_text:
        return

    logger.info(
        "Message from %s (%d), length=%d",
        user.username or user.first_name,
        user.id,
        len(message_text),
    )

    # Continuous typing indicator
    stop_typing = asyncio.Event()

    async def keep_typing():
        while not stop_typing.is_set():
            try:
                await update.message.chat.send_action(ChatAction.TYPING)
            except Exception:
                pass
            try:
                await asyncio.wait_for(stop_typing.wait(), timeout=4.0)
            except asyncio.TimeoutError:
                pass

    typing_task = asyncio.create_task(keep_typing())

    try:
        # Per-user lock prevents concurrent Claude calls for the same user
        async with _get_user_lock(user.id):
            response = await call_claude(message_text, user.id)

        stop_typing.set()
        await typing_task
        await send_rendered(update, response, context)
    except Exception:
        stop_typing.set()
        await typing_task
        raise


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

    logger.info("Starting OpenClaude Telegram bot...")
    logger.info("Allowed users: %s", ALLOWED_USERS)
    logger.info("Working directory: %s", WORKING_DIR)
    logger.info("Session file: %s", SESSION_FILE)

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Register handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("new", cmd_new))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Start polling
    logger.info("Bot is running. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
