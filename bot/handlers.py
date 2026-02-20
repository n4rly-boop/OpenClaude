"""Telegram message/media handlers + batching + streaming UI."""

import asyncio
import html
import re
from datetime import datetime
from pathlib import Path

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from bot.config import (
    ADMIN_USER_ID, ALL_TOOLS, BATCH_WINDOW, STATUS_EDIT_INTERVAL,
    TELEGRAM_MAX_LENGTH, is_authorized,
    get_thread_id,
)
from bot.logging_setup import logger, get_workspace_logger
from bot.sessions import session_key, get_session_id, load_sessions, clear_session
from bot.workspaces import ensure_workspace, get_working_dir
from bot.renderer import TelegramRenderer, split_message
from bot.claude import stream_claude, finished_line, format_tool_status
from bot.sdk_session import sdk_sessions, SDKSession

# Populated at startup via post_init callback
BOT_USERNAME: str = ""

renderer = TelegramRenderer()

# Per-user locks to prevent concurrent Claude calls for the same user
_user_locks: dict[int, asyncio.Lock] = {}


def _get_user_lock(user_id: int) -> asyncio.Lock:
    if user_id not in _user_locks:
        _user_locks[user_id] = asyncio.Lock()
    return _user_locks[user_id]


# ---------------------------------------------------------------------------
# Group / Topic Helpers
# ---------------------------------------------------------------------------

def should_respond(update: Update) -> bool:
    """Decide whether the bot should respond to this message."""
    chat = update.effective_chat
    if chat.type == "private":
        return True

    msg = update.message
    if not msg:
        return False

    thread_id = get_thread_id(update)
    from commands.config import get_respond_mode
    mode = get_respond_mode(chat.id, thread_id)

    if mode == "all":
        return True

    if msg.entities:
        for entity in msg.entities:
            if entity.type == "mention":
                mention = msg.text[entity.offset:entity.offset + entity.length]
                if mention.lower() == f"@{BOT_USERNAME.lower()}":
                    return True

    if msg.reply_to_message and msg.reply_to_message.from_user:
        if msg.reply_to_message.from_user.username and \
           msg.reply_to_message.from_user.username.lower() == BOT_USERNAME.lower():
            return True

    return False


def strip_bot_mention(text: str) -> str:
    """Remove @bot_username from message text."""
    if BOT_USERNAME:
        text = re.sub(rf"@{re.escape(BOT_USERNAME)}\b", "", text, flags=re.IGNORECASE).strip()
    return text


# ---------------------------------------------------------------------------
# Message Sending
# ---------------------------------------------------------------------------

async def send_rendered(
    update: Update,
    text: str,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Render markdown to HTML and send, splitting if needed."""
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


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    from commands import ALL_COMMANDS

    user = update.effective_user
    if not is_authorized(user.id):
        await update.message.reply_text(
            f"Unauthorized. Your user ID is: {user.id}\n"
            "Add it to ALLOWED_USERS in .env to use this bot."
        )
        return

    cmd_lines = [
        "/new \u2014 Start a new conversation",
        "/status \u2014 Show session info",
    ]
    for name, desc in ALL_COMMANDS:
        cmd_lines.append(f"/{name} \u2014 {desc}")

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
    sdk_key = session_key(chat_id, thread_id, session_uid)
    sdk_session = sdk_sessions.pop(sdk_key, None)
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
    sid = get_session_id(chat_id, thread_id, session_uid)
    sessions = load_sessions()
    key = session_key(chat_id, thread_id, session_uid)
    user_data = sessions.get(key, {})

    status_lines = [
        f"<b>OpenClaude Status</b>",
        f"",
        f"<b>User ID:</b> <code>{user.id}</code>",
        f"<b>Username:</b> @{html.escape(user.username) if user.username else 'N/A'}",
        f"<b>Session:</b> <code>{sid or 'None'}</code>",
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
# Message Batching
# ---------------------------------------------------------------------------

_batch_buffers: dict[str, list[str]] = {}
_batch_timers: dict[str, asyncio.TimerHandle] = {}
_batch_updates: dict[str, tuple[Update, ContextTypes.DEFAULT_TYPE]] = {}
_batch_meta: dict[str, tuple[int, int, int]] = {}


async def queue_message(update: Update, context: ContextTypes.DEFAULT_TYPE,
                        chat_id: int, thread_id: int, user_id: int,
                        claude_message: str) -> None:
    """Add a message to the batch buffer. After BATCH_WINDOW seconds of quiet, flush all."""
    session_user_id = user_id if update.effective_chat.type == "private" else 0
    key = session_key(chat_id, thread_id, session_user_id)

    _batch_buffers.setdefault(key, []).append(claude_message)
    _batch_updates[key] = (update, context)
    _batch_meta[key] = (chat_id, thread_id, user_id)

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

    await run_with_streaming(update, context, chat_id, thread_id, user_id, combined)


# ---------------------------------------------------------------------------
# Streaming UI
# ---------------------------------------------------------------------------

async def run_with_streaming(update: Update, context: ContextTypes.DEFAULT_TYPE,
                             chat_id: int, thread_id: int, user_id: int,
                             claude_message: str) -> None:
    """Stream Claude output, show tool progress, then send final response."""
    session_user_id = user_id if update.effective_chat.type == "private" else 0
    tg_thread_id = thread_id or None
    from commands.config import get_streaming, get_verbose
    streaming = get_streaming(chat_id, thread_id)
    show_tools = get_verbose(chat_id, thread_id)
    status_msg = None
    finished_lines: list[str] = []
    current_active: str = ""
    last_edit_time: float = 0

    live_msg = None
    live_text = ""
    last_live_edit: float = 0
    LIVE_EDIT_INTERVAL = 2.0

    async def _update_status(new_active: str = "") -> None:
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
            return

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
            pass

    async def _update_live(text: str) -> None:
        nonlocal live_msg, last_live_edit

        now = asyncio.get_event_loop().time()
        if live_msg and (now - last_live_edit) < LIVE_EDIT_INTERVAL:
            return

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
    in_tool = False

    async with _get_user_lock(session_user_id):
        async for event in stream_claude(claude_message, chat_id, thread_id, session_user_id,
                                         working_dir=chat_working_dir, verbose=streaming):
            etype = event.get("type")

            if etype == "tool_use":
                in_tool = True
                live_text = ""
                if show_tools:
                    if current_active:
                        finished_lines.append(finished_line(current_active))
                    await _update_status(event["status"])

            elif etype == "tool_result":
                in_tool = False
                if show_tools:
                    if current_active:
                        finished_lines.append(finished_line(current_active))
                        await _update_status("")

            elif etype == "partial":
                if not in_tool:
                    live_text += event["text"]
                    await _update_live(live_text)

            elif etype == "result":
                response_text = event.get("text", "")

            elif etype == "error":
                response_text = event.get("text", "An error occurred.")

            elif etype == "silent":
                response_text = ""

    # Clean up status message
    if status_msg:
        try:
            await status_msg.delete()
        except Exception:
            pass

    if response_text is None:
        response_text = "Claude processed the request but returned no text output."

    if not response_text:
        # Silent exit (e.g. bot restart killed the process) — nothing to send
        if live_msg:
            try:
                await live_msg.delete()
            except Exception:
                pass
        return

    if live_msg and streaming:
        try:
            rendered = renderer.render(response_text)
            if len(rendered) <= TELEGRAM_MAX_LENGTH:
                await live_msg.edit_text(
                    rendered,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
            else:
                await live_msg.delete()
                await send_rendered(update, response_text, context)
        except Exception:
            try:
                await live_msg.delete()
            except Exception:
                pass
            await send_rendered(update, response_text, context)
    else:
        await send_rendered(update, response_text, context)


# ---------------------------------------------------------------------------
# Message & Media Handlers
# ---------------------------------------------------------------------------

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

    message_text = strip_bot_mention(message_text)
    if not message_text:
        return

    chat_id = update.effective_chat.id
    thread_id = get_thread_id(update)

    logger.info(
        "Message from %s (%d) in chat %d thread %d, length=%d",
        user.username or user.first_name,
        user.id, chat_id, thread_id, len(message_text),
    )
    get_workspace_logger(chat_id).info(
        "Message from user %d (%s), length=%d",
        user.id, user.username or user.first_name, len(message_text),
    )

    await queue_message(update, context, chat_id, thread_id, user.id, message_text)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming voice messages and audio."""
    from bot.transcribe import transcribe

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
        user.username or user.first_name, user.id,
        chat_id, thread_id, getattr(voice, "duration", "?"),
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

    await queue_message(update, context, chat_id, thread_id, user.id, claude_msg)


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming documents/files."""
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
        user.username or user.first_name, user.id,
        chat_id, thread_id, doc.file_name, doc.file_size,
    )
    get_workspace_logger(chat_id).info(
        "Document from user %d: %s (%s bytes)",
        user.id, doc.file_name, doc.file_size,
    )

    workspace = ensure_workspace(chat_id)
    today = datetime.now().strftime("%Y-%m-%d")
    dest_dir = workspace / "uploads" / f"t{thread_id}" / today
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(doc.file_name).name if doc.file_name else f"file_{doc.file_id}"
    dest = dest_dir / safe_name

    file = await context.bot.get_file(doc.file_id)
    await file.download_to_drive(dest)

    caption = update.message.caption or ""
    claude_msg = f"[File received: {dest.relative_to(workspace)}]"
    if caption:
        claude_msg += f' User says: "{caption}"'

    await queue_message(update, context, chat_id, thread_id, user.id, claude_msg)


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming video messages."""
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
        user.username or user.first_name, user.id,
        chat_id, thread_id, video.file_name or video.file_id, video.file_size,
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

    await queue_message(update, context, chat_id, thread_id, user.id, claude_msg)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming photos."""
    user = update.effective_user
    if not is_authorized(user.id):
        return

    if not should_respond(update):
        return

    photos = update.message.photo
    if not photos:
        return

    photo = photos[-1]

    chat_id = update.effective_chat.id
    thread_id = get_thread_id(update)

    logger.info(
        "Photo from %s (%d) in chat %d thread %d, size=%dx%d",
        user.username or user.first_name, user.id,
        chat_id, thread_id, photo.width, photo.height,
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

    await queue_message(update, context, chat_id, thread_id, user.id, claude_msg)
