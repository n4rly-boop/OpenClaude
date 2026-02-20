"""Utility commands: /model, /whoami, /files, /clean."""

import html
import shutil

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from . import helpers as h

COMMANDS = [
    ("model", "Show or switch the Claude model"),
    ("whoami", "Show what the bot knows about you"),
    ("files", "List files in your workspace"),
    ("clean", "Clean uploaded files"),
]


async def cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show current Claude model, or switch it."""
    user = update.effective_user
    if not h.is_authorized(user.id):
        return

    thread_id = h.get_thread_id(update)
    new_model = " ".join(context.args).strip() if context.args else ""

    if new_model:
        if h.ADMIN_USER_ID and user.id != h.ADMIN_USER_ID:
            await update.message.reply_text(
                "Only admin can switch the model.",
                message_thread_id=thread_id or None,
            )
            return

        h.set_claude_model(new_model)
        await update.message.reply_text(
            f"Model switched to: <code>{html.escape(new_model)}</code>",
            parse_mode=ParseMode.HTML,
            message_thread_id=thread_id or None,
        )
        h.logger.info("Model changed to %s by user %d", new_model, user.id)
    else:
        current = h.get_claude_model() or "(default — not set)"
        await update.message.reply_text(
            f"Current model: <code>{html.escape(current)}</code>\n"
            f"Usage: /model <code>model-name</code> to switch",
            parse_mode=ParseMode.HTML,
            message_thread_id=thread_id or None,
        )


async def cmd_whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the bot's knowledge about the user (USER.md contents)."""
    user = update.effective_user
    if not h.is_authorized(user.id):
        return

    chat_id = update.effective_chat.id
    thread_id = h.get_thread_id(update)
    workspace = h.ensure_workspace(chat_id)
    user_md = workspace / "USER.md"

    if user_md.exists():
        content = user_md.read_text().strip()
        if content:
            text = f"<b>USER.md</b>:\n<pre>{html.escape(content[:3000])}</pre>"
        else:
            text = "USER.md exists but is empty."
    else:
        text = "USER.md not created yet. Start a conversation and the bot will learn about you."

    try:
        await update.message.reply_text(
            text, parse_mode=ParseMode.HTML, message_thread_id=thread_id or None,
        )
    except Exception:
        await update.message.reply_text(
            content if user_md.exists() and content else text,
            message_thread_id=thread_id or None,
        )


async def cmd_files(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List files in the workspace."""
    user = update.effective_user
    if not h.is_authorized(user.id):
        return

    chat_id = update.effective_chat.id
    thread_id = h.get_thread_id(update)
    workspace = h.ensure_workspace(chat_id)

    lines = []
    for item in sorted(workspace.rglob("*")):
        rel = item.relative_to(workspace)
        # Skip .claude directory contents (symlink to shared config)
        if str(rel).startswith(".claude"):
            continue
        depth = len(rel.parts)
        if depth > 4:
            continue
        indent = "  " * (depth - 1)
        if item.is_dir():
            lines.append(f"{indent}{rel.name}/")
        else:
            size = item.stat().st_size
            if size > 1024 * 1024:
                size_str = f"{size / 1024 / 1024:.1f}MB"
            elif size > 1024:
                size_str = f"{size / 1024:.0f}KB"
            else:
                size_str = f"{size}B"
            lines.append(f"{indent}{rel.name} ({size_str})")

    if not lines:
        text = "Workspace is empty."
    else:
        if len(lines) > 60:
            lines = lines[:60]
            lines.append("... and more files")
        text = f"<b>Workspace files</b>:\n<pre>{html.escape(chr(10).join(lines))}</pre>"

    for chunk in h.split_message(text):
        try:
            await update.message.reply_text(
                chunk, parse_mode=ParseMode.HTML, message_thread_id=thread_id or None,
            )
        except Exception:
            await update.message.reply_text(chunk, message_thread_id=thread_id or None)


async def cmd_clean(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clean uploaded files from the workspace."""
    user = update.effective_user
    if not h.is_authorized(user.id):
        return

    chat_id = update.effective_chat.id
    thread_id = h.get_thread_id(update)
    workspace = h.ensure_workspace(chat_id)
    uploads_dir = workspace / "uploads"

    if not uploads_dir.exists():
        await update.message.reply_text(
            "No uploads to clean.", message_thread_id=thread_id or None,
        )
        return

    file_count = sum(1 for _ in uploads_dir.rglob("*") if _.is_file())
    if file_count == 0:
        await update.message.reply_text(
            "Uploads directory is empty.", message_thread_id=thread_id or None,
        )
        return

    total_size = sum(f.stat().st_size for f in uploads_dir.rglob("*") if f.is_file())
    if total_size > 1024 * 1024:
        size_str = f"{total_size / 1024 / 1024:.1f}MB"
    else:
        size_str = f"{total_size / 1024:.0f}KB"

    shutil.rmtree(uploads_dir)
    uploads_dir.mkdir(exist_ok=True)

    await update.message.reply_text(
        f"Cleaned {file_count} file(s) ({size_str}).",
        message_thread_id=thread_id or None,
    )
    h.logger.info("User %d cleaned uploads for chat %d: %d files, %s",
                   user.id, chat_id, file_count, size_str)


def register(app: Application) -> None:
    """Register utility command handlers."""
    app.add_handler(CommandHandler("model", cmd_model))
    app.add_handler(CommandHandler("whoami", cmd_whoami))
    app.add_handler(CommandHandler("files", cmd_files))
    app.add_handler(CommandHandler("clean", cmd_clean))
