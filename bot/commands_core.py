"""Core bot commands: /start, /new, /status, /stop."""

import asyncio
import contextlib
import html
import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from bot.config import (
    ALL_TOOLS,
    get_thread_id,
    is_authorized,
)
from bot.process import kill_active_proc
from bot.sessions import clear_session, get_session_id, load_sessions, session_key
from bot.workspaces import get_working_dir

logger = logging.getLogger(__name__)

# Per-session stop events for /stop command
_stop_events: dict[str, asyncio.Event] = {}

# Per-session streaming task references for /stop cancellation
_streaming_tasks: dict[str, asyncio.Task] = {}


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
        "/stop \u2014 Stop current generation",
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
    """Handle /new command -- clear session and start fresh."""
    user = update.effective_user
    if not is_authorized(user.id):
        return

    chat_id = update.effective_chat.id
    thread_id = get_thread_id(update)
    session_uid = user.id if update.effective_chat.type == "private" else 0
    skey = session_key(chat_id, thread_id, session_uid)

    # Stop any active generation first (same full cleanup as /stop)
    if _stop_events.get(skey):
        await _force_stop_session(skey, chat_id, thread_id, session_uid)

    clear_session(chat_id, thread_id, session_uid)
    await update.message.reply_text(
        "Session cleared. Starting fresh.",
        message_thread_id=thread_id or None,
    )
    logger.info("Session cleared for user %d in chat %d thread %d", user.id, chat_id, thread_id)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /status command -- show user ID and session info."""
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
        "<b>OpenClaude Status</b>",
        "",
        f"<b>User ID:</b> <code>{user.id}</code>",
        f"<b>Username:</b> @{html.escape(user.username) if user.username else 'N/A'}",
        f"<b>Session:</b> <code>{sid or 'None'}</code>",
    ]

    if updated := user_data.get("updated_at"):
        status_lines.append(f"<b>Last active:</b> {updated}")

    chat_dir = get_working_dir(chat_id)
    status_lines.extend([
        "",
        f"<b>Working dir:</b> <code>{chat_dir}</code>",
        f"<b>Allowed tools:</b> {ALL_TOOLS}",
    ])

    await update.message.reply_text(
        "\n".join(status_lines),
        parse_mode=ParseMode.HTML,
        message_thread_id=thread_id or None,
    )


async def _force_stop_session(skey: str, chat_id: int, thread_id: int, session_uid: int) -> None:
    """Cancel streaming task and let it clean up gracefully; hard-kill only as last resort."""
    from bot.chat_lock import force_release_chat_lock
    from bot.sdk_session import _sweep_cancellation_callbacks, sdk_session_manager
    from bot.streams import remove_active_stream

    # 1. Signal the streaming loop to stop
    stop_event = _stop_events.get(skey)
    if stop_event:
        stop_event.set()

    # Kill legacy subprocess backend (not SDK — SDK cleanup is below)
    kill_active_proc(skey)

    # 2. Cancel the streaming task and wait for it to finish gracefully.
    task = _streaming_tasks.get(skey)
    task_finished_cleanly = False
    if task and not task.done():
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=5)
        except TimeoutError:
            task.cancel()  # re-cancel in case the first didn't propagate in time
        except (asyncio.CancelledError, Exception):
            pass
        task_finished_cleanly = task.done()

    # 3. If the task didn't finish (or there was no task), hard-kill as last resort
    sdk_session = sdk_session_manager.get(skey)
    if sdk_session:
        if not task_finished_cleanly:
            sdk_session.hard_kill()
            if sdk_session.connected:
                with contextlib.suppress(Exception):
                    await sdk_session.disconnect()
        sdk_session_manager.pop(skey)

    # 4. Safety net: sweep any orphaned _deliver_cancellation callbacks
    _sweep_cancellation_callbacks()

    # Force cleanup in case the task didn't finish or clean up properly
    _streaming_tasks.pop(skey, None)
    _stop_events.pop(skey, None)
    remove_active_stream(chat_id, thread_id, session_uid)
    force_release_chat_lock(skey)


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /stop command -- cancel current Claude generation."""
    user = update.effective_user
    if not is_authorized(user.id):
        return

    chat_id = update.effective_chat.id
    thread_id = get_thread_id(update)
    session_uid = user.id if update.effective_chat.type == "private" else 0
    skey = session_key(chat_id, thread_id, session_uid)
    tg_thread_id = thread_id or None

    stop_event = _stop_events.get(skey)
    if not stop_event:
        await update.message.reply_text(
            "Nothing to stop.",
            message_thread_id=tg_thread_id,
        )
        return

    await _force_stop_session(skey, chat_id, thread_id, session_uid)

    await update.message.reply_text(
        "Generation stopped.",
        message_thread_id=tg_thread_id,
    )
    logger.info("User %d stopped generation in chat %d thread %d", user.id, chat_id, thread_id)
