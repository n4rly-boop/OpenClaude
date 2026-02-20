"""Session persistence (load/save/clear session IDs)."""

import json
import os
import tempfile
from datetime import datetime

from bot.config import SESSION_FILE
from bot.logging_setup import logger


def load_sessions() -> dict:
    """Load session mapping from disk."""
    if SESSION_FILE.exists():
        try:
            return json.loads(SESSION_FILE.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load sessions: %s", e)
    return {}


def save_sessions(sessions: dict) -> None:
    """Persist session mapping to disk (atomic write with fallback)."""
    data = json.dumps(sessions, indent=2)
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(
            dir=SESSION_FILE.parent, suffix=".tmp"
        )
        with os.fdopen(fd, "w") as f:
            f.write(data)
        os.replace(tmp_path, SESSION_FILE)
        tmp_path = None
    except OSError:
        try:
            SESSION_FILE.write_text(data)
            logger.warning("save_sessions: atomic replace failed, used direct write")
        except OSError as e2:
            logger.error("Failed to save sessions: %s", e2)
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def session_key(chat_id: int, thread_id: int, user_id: int) -> str:
    """Build a composite session key: chat_id:thread_id:user_id."""
    return f"{chat_id}:{thread_id}:{user_id}"


def get_session_id(chat_id: int, thread_id: int, user_id: int) -> str | None:
    """Get the Claude session ID for a given chat/thread/user combination."""
    sessions = load_sessions()
    key = session_key(chat_id, thread_id, user_id)
    return sessions.get(key, {}).get("session_id")


def set_session_id(chat_id: int, thread_id: int, user_id: int, sid: str) -> None:
    """Store a Claude session ID for a given chat/thread/user combination."""
    sessions = load_sessions()
    key = session_key(chat_id, thread_id, user_id)
    sessions.setdefault(key, {})["session_id"] = sid
    sessions[key]["updated_at"] = datetime.now().isoformat()
    save_sessions(sessions)


def clear_session(chat_id: int, thread_id: int, user_id: int) -> None:
    """Clear the session for a chat/thread/user combination, starting fresh."""
    sessions = load_sessions()
    key = session_key(chat_id, thread_id, user_id)
    if key in sessions:
        del sessions[key]
        save_sessions(sessions)
