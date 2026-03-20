"""Telegram message/media handlers + batching + streaming UI.

This module is a thin facade. The actual implementations live in:
  - bot.commands_core    — /start, /new, /status, /stop, _force_stop_session
  - bot.streaming        — run_with_streaming, _typing_loop
  - bot.message_handler  — handle_message
  - bot.attachments      — file marker parsing, image URL extraction
  - bot.telegram_sender  — send_file_group, send_single_file, send_rendered
  - bot.media            — normalize_image
  - bot.routing          — should_respond, strip_bot_mention, get_reply_prefix
  - bot.batching         — queue_message, _flush_batch
  - bot.media_handlers   — handle_photo, handle_voice, handle_document, handle_video
  - bot.streaming_session — StreamingSession class
"""

# --- Re-exports from extracted modules (for backward compatibility) ---
# BOT_USERNAME is set from routing module; kept as alias for app.py compatibility
import bot.routing as _routing  # noqa: E402
from bot.attachments import (  # noqa: F401
    extract_image_urls as _extract_image_urls,
)
from bot.batching import queue_message  # noqa: F401
from bot.commands_core import (  # noqa: F401
    _force_stop_session,
    _stop_events,
    _streaming_tasks,
    cmd_new,
    cmd_start,
    cmd_status,
    cmd_stop,
)
from bot.media_handlers import (  # noqa: F401
    handle_document,
    handle_photo,
    handle_video,
    handle_voice,
)
from bot.message_handler import handle_message  # noqa: F401
from bot.routing import (  # noqa: F401
    get_reply_prefix,
    should_respond,
    strip_bot_mention,
)
from bot.streaming import run_with_streaming  # noqa: F401
from bot.telegram_sender import (  # noqa: F401
    send_file_group as _send_file_group,
)

BOT_USERNAME = _routing.BOT_USERNAME


def _set_bot_username(username: str) -> None:
    """Set bot username in both this module and routing module."""
    global BOT_USERNAME
    BOT_USERNAME = username
    _routing.BOT_USERNAME = username
