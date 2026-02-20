"""Active stream tracking (file-backed for crash recovery)."""

import json
import os
import tempfile

from bot.config import ACTIVE_STREAMS_FILE
from bot.logging_setup import logger
from bot.sessions import session_key


def save_active_streams(streams: dict) -> None:
    """Atomic write of active streams to disk."""
    try:
        fd, tmp_path = tempfile.mkstemp(
            dir=ACTIVE_STREAMS_FILE.parent, suffix=".tmp"
        )
        with os.fdopen(fd, "w") as f:
            json.dump(streams, f, indent=2)
        os.replace(tmp_path, ACTIVE_STREAMS_FILE)
    except OSError as e:
        logger.error("Failed to save active streams: %s", e)


def load_active_streams() -> dict:
    """Read active streams from disk."""
    if ACTIVE_STREAMS_FILE.exists():
        try:
            return json.loads(ACTIVE_STREAMS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def add_active_stream(chat_id: int, thread_id: int, user_id: int) -> None:
    """Register a stream start. Survives crashes because it's on disk."""
    streams = load_active_streams()
    key = session_key(chat_id, thread_id, user_id)
    streams[key] = {"chat_id": chat_id, "thread_id": thread_id, "user_id": user_id}
    save_active_streams(streams)


def remove_active_stream(chat_id: int, thread_id: int, user_id: int) -> None:
    """Remove a completed stream. Deletes file when empty."""
    streams = load_active_streams()
    key = session_key(chat_id, thread_id, user_id)
    streams.pop(key, None)
    if streams:
        save_active_streams(streams)
    else:
        ACTIVE_STREAMS_FILE.unlink(missing_ok=True)
