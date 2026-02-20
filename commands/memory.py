"""Memory commands: /forget, /memory, /save, /remember, /history."""

import html
from datetime import datetime
from pathlib import Path

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from . import helpers as h

# (command_name, description) — used by /start listing
COMMANDS = [
    ("memory", "Show current memory contents"),
    ("save", "Save a note to today's daily log"),
    ("remember", "Save a note to long-term memory"),
    ("forget", "Ask Claude to remove something from memory"),
    ("history", "Summarize recent conversation"),
]


def _get_memory_paths(chat_id: int, thread_id: int) -> dict[str, Path]:
    """Return paths to all memory files for a chat/thread."""
    workspace = h.ensure_workspace(chat_id)
    mem_dir = workspace / "memory"
    return {
        "shared": mem_dir / "MEMORY.md",
        "topic": mem_dir / f"t{thread_id}" / "MEMORY.md",
        "daily": mem_dir / f"t{thread_id}" / f"{datetime.now():%Y-%m-%d}.md",
    }


async def cmd_memory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show contents of all memory files for this chat/thread."""
    user = update.effective_user
    if not h.is_authorized(user.id):
        return

    chat_id = update.effective_chat.id
    thread_id = h.get_thread_id(update)
    paths = _get_memory_paths(chat_id, thread_id)

    sections = []
    for label, path in paths.items():
        if path.exists():
            content = path.read_text().strip()
            if content:
                sections.append(
                    f"<b>{html.escape(label)} memory</b> ({path.name}):\n"
                    f"<pre>{html.escape(content[:1500])}</pre>"
                )
            else:
                sections.append(f"<b>{html.escape(label)} memory</b> ({path.name}): <i>empty</i>")
        else:
            sections.append(f"<b>{html.escape(label)} memory</b>: <i>not created yet</i>")

    text = "\n\n".join(sections)
    tg_thread = thread_id or None

    for chunk in h.split_message(text):
        try:
            await update.message.reply_text(
                chunk, parse_mode=ParseMode.HTML, message_thread_id=tg_thread,
            )
        except Exception:
            await update.message.reply_text(chunk, message_thread_id=tg_thread)


async def cmd_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Save a note to today's daily log."""
    user = update.effective_user
    if not h.is_authorized(user.id):
        return

    note = " ".join(context.args) if context.args else ""
    if not note:
        await update.message.reply_text("Usage: /save <note>")
        return

    chat_id = update.effective_chat.id
    thread_id = h.get_thread_id(update)
    paths = _get_memory_paths(chat_id, thread_id)
    daily = paths["daily"]
    daily.parent.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%H:%M")
    entry = f"- [{timestamp}] {note}\n"

    with open(daily, "a") as f:
        f.write(entry)

    await update.message.reply_text(
        "Saved to daily log.",
        message_thread_id=thread_id or None,
    )


async def cmd_remember(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Save a note to long-term shared memory (MEMORY.md)."""
    user = update.effective_user
    if not h.is_authorized(user.id):
        return

    note = " ".join(context.args) if context.args else ""
    if not note:
        await update.message.reply_text("Usage: /remember <note>")
        return

    chat_id = update.effective_chat.id
    thread_id = h.get_thread_id(update)
    paths = _get_memory_paths(chat_id, thread_id)
    mem = paths["shared"]
    mem.parent.mkdir(parents=True, exist_ok=True)

    date = datetime.now().strftime("%Y-%m-%d")
    entry = f"- [{date}] {note}\n"

    with open(mem, "a") as f:
        f.write(entry)

    await update.message.reply_text(
        "Saved to long-term memory.",
        message_thread_id=thread_id or None,
    )


async def cmd_forget(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ask Claude to intelligently remove something from memory files."""
    user = update.effective_user
    if not h.is_authorized(user.id):
        return

    what = " ".join(context.args) if context.args else ""
    if not what:
        await update.message.reply_text("Usage: /forget <what to forget>")
        return

    chat_id = update.effective_chat.id
    thread_id = h.get_thread_id(update)

    prompt = (
        f"[System command: /forget]\n"
        f"The user wants you to remove the following from your memory files: \"{what}\"\n\n"
        f"Read your memory files (memory/MEMORY.md, memory/t{thread_id}/MEMORY.md, "
        f"memory/t{thread_id}/*.md) and remove any entries matching what the user described. "
        f"Use the Edit tool to surgically remove only the relevant lines. "
        f"Then confirm what you removed."
    )

    await h._run_with_streaming(update, context, chat_id, thread_id, user.id, prompt)


async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ask Claude to summarize the recent conversation."""
    user = update.effective_user
    if not h.is_authorized(user.id):
        return

    chat_id = update.effective_chat.id
    thread_id = h.get_thread_id(update)

    prompt = (
        "[System command: /history]\n"
        "The user wants a summary of this conversation. "
        "Provide a concise summary of the key topics discussed, decisions made, "
        "and any pending items. Keep it brief — this is for quick reference."
    )

    await h._run_with_streaming(update, context, chat_id, thread_id, user.id, prompt)


def register(app: Application) -> None:
    """Register memory command handlers."""
    app.add_handler(CommandHandler("memory", cmd_memory))
    app.add_handler(CommandHandler("save", cmd_save))
    app.add_handler(CommandHandler("remember", cmd_remember))
    app.add_handler(CommandHandler("forget", cmd_forget))
    app.add_handler(CommandHandler("history", cmd_history))
