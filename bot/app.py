"""Application builder, post_init, post_shutdown, main()."""

import asyncio
import atexit
import json
import re
import sys

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
)

from bot.config import (
    ALLOWED_USERS, ACTIVE_STREAMS_FILE, RESTART_STATE_FILE,
    SESSION_FILE, TELEGRAM_BOT_TOKEN, WORKING_DIR,
)
from bot.logging_setup import logger, infra_logger
from bot.sessions import get_session_id
from bot.streams import load_active_streams
from bot.workspaces import get_working_dir
from bot.renderer import TelegramRenderer, split_message
from bot.claude import stream_claude
from bot.sdk_session import HAS_SDK, cleanup_idle_sessions, shutdown_sdk_sessions
from bot import handlers
from commands import register_all, ALL_COMMANDS


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

    renderer = TelegramRenderer()

    async def post_init(application: Application) -> None:
        """Fetch bot info at startup and resume interrupted generations."""
        bot = application.bot
        me = await bot.get_me()
        handlers.BOT_USERNAME = me.username or ""
        logger.info("Bot username: @%s", handlers.BOT_USERNAME)
        infra_logger.info("Bot username: @%s", handlers.BOT_USERNAME)

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

        if HAS_SDK:
            asyncio.create_task(cleanup_idle_sessions())
            logger.info("SDK idle session cleanup task started")

        # Collect interrupted chats from restart state and active streams
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
                                parse_mode="HTML",
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
            await shutdown_sdk_sessions()
            infra_logger.info("SDK sessions shut down")

    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # Register handlers
    app.add_handler(CommandHandler("start", handlers.cmd_start))
    app.add_handler(CommandHandler("new", handlers.cmd_new))
    app.add_handler(CommandHandler("status", handlers.cmd_status))
    register_all(app)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_message))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handlers.handle_voice))
    app.add_handler(MessageHandler(filters.VIDEO, handlers.handle_video))
    app.add_handler(MessageHandler(filters.Document.ALL, handlers.handle_document))
    app.add_handler(MessageHandler(filters.PHOTO, handlers.handle_photo))

    # Start polling
    logger.info("Bot is running. Press Ctrl+C to stop.")
    infra_logger.info("Bot running")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
