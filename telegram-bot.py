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
import shutil
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
UPLOADS_DIR = SCRIPT_DIR / "uploads"

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
# Claude CLI Integration
# ---------------------------------------------------------------------------


async def call_claude(message: str, chat_id: int, thread_id: int, user_id: int) -> str:
    """Call the Claude CLI with a message and return the response text."""
    session_id = get_session_id(chat_id, thread_id, user_id)

    # On first message in a session, prepend instructions to read prompt files
    if not session_id:
        preamble = (
            "You are starting a new session. Read CLAUDE.md first, "
            "then follow its startup sequence before responding. "
            "The user's message is:\n\n"
        )
        message = preamble + message

    claude_bin = shutil.which("claude") or "/root/.local/bin/claude"
    logger.info("Using claude binary: %s (exists: %s)", claude_bin, os.path.isfile(claude_bin))
    cmd = [
        claude_bin,
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
        env = os.environ.copy()
        # Ensure claude CLI is findable even when launched from systemd/cron
        local_bin = str(Path.home() / ".local" / "bin")
        if local_bin not in env.get("PATH", ""):
            env["PATH"] = local_bin + ":" + env.get("PATH", "/usr/bin:/bin")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=WORKING_DIR,
            env=env,
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
            set_session_id(chat_id, thread_id, user_id, new_session_id)
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

    except FileNotFoundError as e:
        logger.exception("FileNotFoundError in call_claude: %s", e)
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
    thread_id = get_thread_id(update)

    for chunk in chunks:
        try:
            await update.message.reply_text(
                chunk,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                message_thread_id=thread_id or None,
            )
        except Exception:
            logger.warning("HTML parse failed for chunk, falling back to plain text")
            plain = re.sub(r"<[^>]+>", "", chunk)
            await update.message.reply_text(
                plain,
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

    status_lines.extend([
        f"",
        f"<b>Working dir:</b> <code>{WORKING_DIR}</code>",
        f"<b>Allowed tools:</b> {ALLOWED_TOOLS}",
    ])

    await update.message.reply_text(
        "\n".join(status_lines),
        parse_mode=ParseMode.HTML,
        message_thread_id=thread_id or None,
    )


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
            response = await call_claude(message_text, chat_id, thread_id, user.id)

        stop_typing.set()
        await typing_task
        await send_rendered(update, response, context)
    except Exception:
        stop_typing.set()
        await typing_task
        raise


async def _run_with_typing(update: Update, context: ContextTypes.DEFAULT_TYPE,
                           chat_id: int, thread_id: int, user_id: int,
                           claude_message: str) -> None:
    """Shared helper: show typing indicator, call Claude under per-user lock, send response."""
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
        async with _get_user_lock(user_id):
            response = await call_claude(claude_message, chat_id, thread_id, user_id)

        stop_typing.set()
        await typing_task
        await send_rendered(update, response, context)
    except Exception:
        stop_typing.set()
        await typing_task
        raise


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

    await _run_with_typing(update, context, chat_id, thread_id, user.id, claude_msg)


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

    await _run_with_typing(update, context, chat_id, thread_id, user.id, claude_msg)


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

    await _run_with_typing(update, context, chat_id, thread_id, user.id, claude_msg)


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

    async def post_init(application: Application) -> None:
        """Fetch bot info at startup so we know our username."""
        global BOT_USERNAME
        bot = application.bot
        me = await bot.get_me()
        BOT_USERNAME = me.username or ""
        logger.info("Bot username: @%s", BOT_USERNAME)

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()

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
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
