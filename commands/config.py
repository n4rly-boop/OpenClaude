"""Config commands: /stream, /respond — with inline keyboard toggles."""

import json
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

from bot.config import SCRIPT_DIR, is_authorized, get_thread_id
from bot.logging_setup import logger

COMMANDS = [
    ("stream", "Toggle live streaming of Claude's response"),
    ("verbose", "Toggle tool usage display"),
    ("respond", "Set group response mode (mention/all)"),
]

# ---------------------------------------------------------------------------
# Persistent Settings (per chat/thread)
# ---------------------------------------------------------------------------

_SETTINGS_FILE = None  # Set lazily


def _settings_file() -> Path:
    global _SETTINGS_FILE
    if _SETTINGS_FILE is None:
        _SETTINGS_FILE = SCRIPT_DIR / ".chat-settings.json"
    return _SETTINGS_FILE


def _load_settings() -> dict:
    f = _settings_file()
    if f.exists():
        try:
            return json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_settings(settings: dict) -> None:
    try:
        _settings_file().write_text(json.dumps(settings, indent=2))
    except OSError as e:
        logger.error("Failed to save chat settings: %s", e)


def _setting_key(chat_id: int, thread_id: int) -> str:
    return f"{chat_id}:{thread_id}"


def get_streaming(chat_id: int, thread_id: int) -> bool:
    """Get live streaming setting for a chat/thread."""
    settings = _load_settings()
    key = _setting_key(chat_id, thread_id)
    return settings.get(key, {}).get("streaming", False)


def get_verbose(chat_id: int, thread_id: int) -> bool:
    """Get verbose (tool progress) setting for a chat/thread. Default: ON."""
    settings = _load_settings()
    key = _setting_key(chat_id, thread_id)
    return settings.get(key, {}).get("verbose", True)


def get_respond_mode(chat_id: int, thread_id: int) -> str:
    """Get response mode for a chat/thread: 'mention' or 'all'."""
    settings = _load_settings()
    key = _setting_key(chat_id, thread_id)
    return settings.get(key, {}).get("respond_mode", "mention")


def _set_setting(chat_id: int, thread_id: int, name: str, value) -> None:
    settings = _load_settings()
    key = _setting_key(chat_id, thread_id)
    settings.setdefault(key, {})[name] = value
    _save_settings(settings)


# ---------------------------------------------------------------------------
# /stream — toggle live response streaming
# ---------------------------------------------------------------------------

def _stream_keyboard(is_on: bool, chat_id: int, thread_id: int) -> InlineKeyboardMarkup:
    on_label = "\u2713 ON" if is_on else "ON"
    off_label = "\u2713 OFF" if not is_on else "OFF"
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(on_label, callback_data=f"stream:on:{chat_id}:{thread_id}"),
        InlineKeyboardButton(off_label, callback_data=f"stream:off:{chat_id}:{thread_id}"),
    ]])


def _stream_text(is_on: bool) -> str:
    state = "ON" if is_on else "OFF"
    if is_on:
        desc = "Response appears live as Claude types, then gets replaced with the final formatted version."
    else:
        desc = "Tool progress is shown while working, then the full response appears at once."
    return f"<b>Streaming:</b> {state}\n\n{desc}"


async def cmd_stream(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_authorized(user.id):
        return

    chat_id = update.effective_chat.id
    thread_id = get_thread_id(update)
    is_on = get_streaming(chat_id, thread_id)

    await update.message.reply_text(
        _stream_text(is_on),
        parse_mode=ParseMode.HTML,
        reply_markup=_stream_keyboard(is_on, chat_id, thread_id),
        message_thread_id=thread_id or None,
    )


async def callback_stream(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    if len(parts) != 4:
        return
    _, action, chat_id_str, thread_id_str = parts
    chat_id = int(chat_id_str)
    thread_id = int(thread_id_str)

    new_value = action == "on"
    _set_setting(chat_id, thread_id, "streaming", new_value)

    await query.edit_message_text(
        _stream_text(new_value),
        parse_mode=ParseMode.HTML,
        reply_markup=_stream_keyboard(new_value, chat_id, thread_id),
    )


# ---------------------------------------------------------------------------
# /verbose — toggle tool usage display
# ---------------------------------------------------------------------------

def _verbose_keyboard(is_on: bool, chat_id: int, thread_id: int) -> InlineKeyboardMarkup:
    on_label = "\u2713 ON" if is_on else "ON"
    off_label = "\u2713 OFF" if not is_on else "OFF"
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(on_label, callback_data=f"verbose:on:{chat_id}:{thread_id}"),
        InlineKeyboardButton(off_label, callback_data=f"verbose:off:{chat_id}:{thread_id}"),
    ]])


def _verbose_text(is_on: bool) -> str:
    state = "ON" if is_on else "OFF"
    if is_on:
        desc = "Shows tool usage while Claude is working (reading files, running commands, etc.)."
    else:
        desc = "No tool progress shown — only the final response."
    return f"<b>Tool display:</b> {state}\n\n{desc}"


async def cmd_verbose(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_authorized(user.id):
        return

    chat_id = update.effective_chat.id
    thread_id = get_thread_id(update)
    is_on = get_verbose(chat_id, thread_id)

    await update.message.reply_text(
        _verbose_text(is_on),
        parse_mode=ParseMode.HTML,
        reply_markup=_verbose_keyboard(is_on, chat_id, thread_id),
        message_thread_id=thread_id or None,
    )


async def callback_verbose(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    if len(parts) != 4:
        return
    _, action, chat_id_str, thread_id_str = parts
    chat_id = int(chat_id_str)
    thread_id = int(thread_id_str)

    new_value = action == "on"
    _set_setting(chat_id, thread_id, "verbose", new_value)

    await query.edit_message_text(
        _verbose_text(new_value),
        parse_mode=ParseMode.HTML,
        reply_markup=_verbose_keyboard(new_value, chat_id, thread_id),
    )


# ---------------------------------------------------------------------------
# /respond — group response mode (mention / all)
# ---------------------------------------------------------------------------

def _respond_keyboard(mode: str, chat_id: int, thread_id: int) -> InlineKeyboardMarkup:
    mention_label = ("\u2713 " if mode == "mention" else "") + "Mention only"
    all_label = ("\u2713 " if mode == "all" else "") + "All messages"
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(mention_label, callback_data=f"respond:mention:{chat_id}:{thread_id}"),
        InlineKeyboardButton(all_label, callback_data=f"respond:all:{chat_id}:{thread_id}"),
    ]])


def _respond_text(mode: str) -> str:
    if mode == "all":
        desc = "Bot responds to <b>every message</b> in this thread."
    else:
        desc = "Bot responds only when <b>@mentioned</b> or <b>replied to</b>."
    return f"<b>Response mode:</b> {mode}\n\n{desc}"


async def cmd_respond(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_authorized(user.id):
        return

    chat_id = update.effective_chat.id
    thread_id = get_thread_id(update)

    if update.effective_chat.type == "private":
        await update.message.reply_text("Response mode only applies to group chats.")
        return

    mode = get_respond_mode(chat_id, thread_id)

    await update.message.reply_text(
        _respond_text(mode),
        parse_mode=ParseMode.HTML,
        reply_markup=_respond_keyboard(mode, chat_id, thread_id),
        message_thread_id=thread_id or None,
    )


async def callback_respond(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    if len(parts) != 4:
        return
    _, mode, chat_id_str, thread_id_str = parts
    chat_id = int(chat_id_str)
    thread_id = int(thread_id_str)

    if mode not in ("mention", "all"):
        return

    _set_setting(chat_id, thread_id, "respond_mode", mode)

    await query.edit_message_text(
        _respond_text(mode),
        parse_mode=ParseMode.HTML,
        reply_markup=_respond_keyboard(mode, chat_id, thread_id),
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(app: Application) -> None:
    app.add_handler(CommandHandler("stream", cmd_stream))
    app.add_handler(CommandHandler("verbose", cmd_verbose))
    app.add_handler(CommandHandler("respond", cmd_respond))
    app.add_handler(CallbackQueryHandler(callback_stream, pattern=r"^stream:"))
    app.add_handler(CallbackQueryHandler(callback_verbose, pattern=r"^verbose:"))
    app.add_handler(CallbackQueryHandler(callback_respond, pattern=r"^respond:"))
