"""Admin commands: /sessions, /restart, /logs, /usage."""

import html
import json
import subprocess

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from bot.config import (
    ADMIN_USER_ID, ALLOWED_USERS, RESTART_MESSAGES_FILE, SCRIPT_DIR,
    WORKSPACES_DIR, LOGS_DIR,
    is_authorized, get_claude_model, get_thread_id,
)
from bot.logging_setup import logger, infra_logger
from bot.sessions import load_sessions
from bot.streams import load_active_streams
from bot.renderer import split_message

COMMANDS = [
    ("sessions", "List all active sessions (admin)"),
    ("restart", "Graceful bot restart (admin)"),
    ("logs", "Show recent infrastructure logs (admin)"),
    ("usage", "Show usage statistics (admin)"),
]


def _require_admin(user_id: int) -> bool:
    return ADMIN_USER_ID is not None and user_id == ADMIN_USER_ID


async def cmd_sessions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List all active sessions across chats."""
    user = update.effective_user
    if not is_authorized(user.id):
        return
    if not _require_admin(user.id):
        await update.message.reply_text("Admin only.")
        return

    thread_id = get_thread_id(update)
    sessions = load_sessions()

    if not sessions:
        await update.message.reply_text(
            "No active sessions.", message_thread_id=thread_id or None,
        )
        return

    lines = [f"<b>Active Sessions ({len(sessions)})</b>\n"]
    for key, data in sessions.items():
        sid = data.get("session_id", "?")
        updated = data.get("updated_at", "?")
        lines.append(
            f"<code>{html.escape(key)}</code>\n"
            f"  session: <code>{html.escape(sid[:16])}...</code>\n"
            f"  updated: {html.escape(str(updated))}"
        )

    text = "\n".join(lines)
    for chunk in split_message(text):
        try:
            await update.message.reply_text(
                chunk, parse_mode=ParseMode.HTML, message_thread_id=thread_id or None,
            )
        except Exception:
            await update.message.reply_text(chunk, message_thread_id=thread_id or None)


async def cmd_restart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Trigger a graceful bot restart via restart.sh."""
    user = update.effective_user
    if not is_authorized(user.id):
        return
    if not _require_admin(user.id):
        await update.message.reply_text("Admin only.")
        return

    thread_id = get_thread_id(update)
    restart_script = SCRIPT_DIR / "bin" / "restart.sh"

    if not restart_script.exists():
        await update.message.reply_text(
            "restart.sh not found.", message_thread_id=thread_id or None,
        )
        return

    sent = await update.message.reply_text(
        "Restarting bot...", message_thread_id=thread_id or None,
    )
    infra_logger.info("Restart triggered via /restart by user %d", user.id)

    # Save message ID so post_init can edit it to "Restart complete"
    entry = {
        "chat_id": sent.chat_id,
        "thread_id": thread_id or 0,
        "message_id": sent.message_id,
    }
    # Merge with any existing entries (from notify-interrupted.sh)
    existing = []
    if RESTART_MESSAGES_FILE.exists():
        try:
            existing = json.loads(RESTART_MESSAGES_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    existing.append(entry)
    RESTART_MESSAGES_FILE.write_text(json.dumps(existing))

    subprocess.Popen(
        ["bash", str(restart_script)],
        cwd=str(SCRIPT_DIR),
        start_new_session=True,
    )


async def cmd_logs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show recent infrastructure log entries."""
    user = update.effective_user
    if not is_authorized(user.id):
        return
    if not _require_admin(user.id):
        await update.message.reply_text("Admin only.")
        return

    thread_id = get_thread_id(update)
    arg = " ".join(context.args).strip() if context.args else ""

    if arg.startswith("c"):
        try:
            chat_id = int(arg[1:])
        except ValueError:
            await update.message.reply_text(
                "Usage: /logs or /logs c<chat_id>",
                message_thread_id=thread_id or None,
            )
            return
        log_path = WORKSPACES_DIR / f"c{chat_id}" / "logs" / "activity.log"
    else:
        log_path = LOGS_DIR / "infra.log"

    if not log_path.exists():
        await update.message.reply_text(
            f"Log file not found: {log_path.name}",
            message_thread_id=thread_id or None,
        )
        return

    content = log_path.read_text()
    lines = content.strip().split("\n")
    tail = lines[-50:]
    text = (
        f"<b>{html.escape(log_path.name)}</b> (last {len(tail)} lines):\n"
        f"<pre>{html.escape(chr(10).join(tail))}</pre>"
    )

    for chunk in split_message(text):
        try:
            await update.message.reply_text(
                chunk, parse_mode=ParseMode.HTML, message_thread_id=thread_id or None,
            )
        except Exception:
            plain = chunk.replace("<pre>", "").replace("</pre>", "")
            plain = plain.replace("<b>", "").replace("</b>", "")
            await update.message.reply_text(plain, message_thread_id=thread_id or None)


async def cmd_usage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show usage statistics."""
    user = update.effective_user
    if not is_authorized(user.id):
        return
    if not _require_admin(user.id):
        await update.message.reply_text("Admin only.")
        return

    thread_id = get_thread_id(update)
    sessions = load_sessions()

    workspace_count = 0
    total_upload_size = 0
    total_memory_size = 0
    if WORKSPACES_DIR.exists():
        for ws in WORKSPACES_DIR.iterdir():
            if ws.is_dir() and ws.name.startswith("c"):
                workspace_count += 1
                uploads = ws / "uploads"
                if uploads.exists():
                    total_upload_size += sum(
                        f.stat().st_size for f in uploads.rglob("*") if f.is_file()
                    )
                mem = ws / "memory"
                if mem.exists():
                    total_memory_size += sum(
                        f.stat().st_size for f in mem.rglob("*") if f.is_file()
                    )

    def _fmt(b: int) -> str:
        if b > 1024 * 1024:
            return f"{b / 1024 / 1024:.1f}MB"
        if b > 1024:
            return f"{b / 1024:.0f}KB"
        return f"{b}B"

    streams = load_active_streams()

    lines = [
        "<b>Usage Statistics</b>",
        "",
        f"<b>Sessions:</b> {len(sessions)}",
        f"<b>Active streams:</b> {len(streams)}",
        f"<b>Workspaces:</b> {workspace_count}",
        f"<b>Uploads size:</b> {_fmt(total_upload_size)}",
        f"<b>Memory size:</b> {_fmt(total_memory_size)}",
        f"<b>Model:</b> <code>{html.escape(get_claude_model() or 'default')}</code>",
        f"<b>Allowed users:</b> {len(ALLOWED_USERS)}",
    ]

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        message_thread_id=thread_id or None,
    )


def register(app: Application) -> None:
    """Register admin command handlers."""
    app.add_handler(CommandHandler("sessions", cmd_sessions))
    app.add_handler(CommandHandler("restart", cmd_restart))
    app.add_handler(CommandHandler("logs", cmd_logs))
    app.add_handler(CommandHandler("usage", cmd_usage))
