"""Streaming UI — run_with_streaming, typing loop, stale status check."""

import asyncio
import contextlib
import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot.chat_lock import get_chat_lock, release_chat_lock_ref, set_lock_holder
from bot.claude import stream_claude
from bot.commands_core import _stop_events, _streaming_tasks
from bot.rollback import get_rollback_injection
from bot.sessions import get_context_pct, get_session_id, session_key
from bot.workspaces import get_working_dir

logger = logging.getLogger(__name__)

TYPING_INTERVAL = 4  # seconds between typing indicator refreshes


def _get_stale_status_msg_id(chat_id: int, thread_id: int, user_id: int) -> int | None:
    """Check for a persisted status_msg_id from a previous crashed/interrupted session."""
    from bot.streams import load_active_streams
    skey = session_key(chat_id, thread_id, user_id)
    streams = load_active_streams()
    entry = streams.get(skey)
    if isinstance(entry, dict):
        return entry.get("status_msg_id")
    return None


async def _typing_loop(bot, chat_id: int, thread_id: int | None = None) -> None:
    """Send 'typing' chat action periodically until cancelled."""
    try:
        while True:
            try:
                await bot.send_chat_action(
                    chat_id=chat_id,
                    action="typing",
                    message_thread_id=thread_id if thread_id else None,
                )
            except Exception:
                logger.debug("typing indicator send failed for chat %d", chat_id)
            await asyncio.sleep(TYPING_INTERVAL)
    except asyncio.CancelledError:
        pass


async def run_with_streaming(update: Update, context: ContextTypes.DEFAULT_TYPE,
                             chat_id: int, thread_id: int, user_id: int,
                             claude_message: str, _is_compact: bool = False) -> None:
    """Stream Claude output, show tool progress, then send final response.

    All streaming state and event handling is delegated to StreamingSession.
    This function manages the lifecycle: lock acquisition, typing indicator,
    event loop, and cleanup.
    """
    from bot.streaming_session import StreamingSession
    from commands.config import get_streaming, get_verbose

    session_user_id = user_id if update.effective_chat.type == "private" else 0
    tg_thread_id = thread_id or None
    streaming = get_streaming(chat_id, thread_id)
    show_tools = get_verbose(chat_id, thread_id)

    session = StreamingSession(
        update=update,
        context=context,
        chat_id=chat_id,
        thread_id=thread_id,
        tg_thread_id=tg_thread_id,
        streaming=streaming,
        show_tools=show_tools,
    )
    # Store session_user_id for usage tracking inside _on_result
    session.session_user_id = session_user_id

    chat_working_dir = get_working_dir(chat_id)
    skey = session_key(chat_id, thread_id, session_user_id)

    typing_task = None
    lock = None if _is_compact else get_chat_lock(skey)

    if lock is not None:
        try:
            await asyncio.wait_for(lock.acquire(), timeout=30)
        except TimeoutError:
            logger.error(
                "Lock acquisition timed out for user %d in chat %d",
                session_user_id, chat_id,
            )
            release_chat_lock_ref(skey)
            with contextlib.suppress(Exception):
                await update.message.reply_text(
                    "Still processing a previous request. Use /stop to cancel it.",
                    message_thread_id=tg_thread_id,
                )
            return
        set_lock_holder(skey)

    try:
        # Delete any orphaned status message from a previous crashed session
        from bot.streams import clear_stream_status_msg_id
        stale_status_id = _get_stale_status_msg_id(chat_id, thread_id, session_user_id)
        if stale_status_id:
            with contextlib.suppress(Exception):
                await context.bot.delete_message(chat_id=chat_id, message_id=stale_status_id)
            clear_stream_status_msg_id(chat_id, thread_id, session_user_id)

        # Only register task/stop_event for primary calls -- auto-compact is nested
        # and must not clobber the outer call's registration.
        if not _is_compact:
            _streaming_tasks[skey] = asyncio.current_task()
        stop_event = asyncio.Event()
        if not _is_compact:
            _stop_events[skey] = stop_event

        # Check for rollback injection
        _session_exists = get_session_id(chat_id, thread_id, session_user_id) is not None
        _rollback_text = get_rollback_injection(skey, _session_exists)
        if _rollback_text:
            # Prepend rollback info to the user message
            claude_message = _rollback_text + "\n\n" + claude_message

        # Start typing indicator -- runs until cancelled
        typing_task = asyncio.create_task(_typing_loop(context.bot, chat_id, tg_thread_id))
        finalized = False
        try:
            async for event in stream_claude(claude_message, chat_id, thread_id, session_user_id,
                                             working_dir=chat_working_dir, verbose=streaming,
                                             stop_event=stop_event,
                                             real_user_id=user_id):
                etype = event.get("type")

                # Stop typing once visible output is streaming
                if etype == "partial" and typing_task and not typing_task.done():
                    typing_task.cancel()

                await session.handle_event(event)

                # Finalize immediately on result — the SDK stream may hang
                # after ResultMessage, so don't wait for the loop to end.
                if etype == "result" and not finalized:
                    await session.flush_live()
                    await session.finalize_response()
                    finalized = True

            # Final flush if stream ended without result event
            if not finalized:
                await session.flush_live()
        finally:
            if not _is_compact:
                _stop_events.pop(skey, None)

        # Handle /stop cancellation (cleanup happens in finally below)
        if session.stopped:
            return

        # Finalize if not already done inside the loop
        if not finalized:
            await session.finalize_response()

        # Context usage warnings and auto-compact (also inside lock)
        if not _is_compact:
            sid = get_session_id(chat_id, thread_id, session_user_id)
            ctx = get_context_pct(chat_id, thread_id, session_user_id) if sid else None
            if ctx:
                pct, used, window = ctx
                if pct >= 0.8:
                    await update.message.reply_text(
                        f"Context at {pct:.0%} \u2014 auto-compacting\u2026",
                        message_thread_id=tg_thread_id,
                    )
                    await run_with_streaming(update, context, chat_id, thread_id,
                                             user_id, "/compact", _is_compact=True)
                elif pct >= 0.6:
                    await update.message.reply_text(
                        f"Context at {pct:.0%} \u2014 consider using /compact",
                        message_thread_id=tg_thread_id,
                    )
    except asyncio.CancelledError:
        session.stopped = True
        raise
    finally:
        # Always cancel typing indicator
        if typing_task and not typing_task.done():
            typing_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await typing_task
        if not _is_compact:
            _streaming_tasks.pop(skey, None)
        if lock is not None:
            with contextlib.suppress(RuntimeError):
                lock.release()
            release_chat_lock_ref(skey)
        # Clean up Telegram messages (must run even on CancelledError)
        await session.cleanup_status()
        if session.stopped:
            await session.cleanup_on_stop()
