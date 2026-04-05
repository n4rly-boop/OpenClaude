"""Session persistence (load/save/clear session IDs)."""

import sys
from datetime import datetime

from bot.cache import FileBackedCache
from bot.config import SESSION_FILE  # noqa: F401 — kept so patches on the module attr work

# ---------------------------------------------------------------------------
# In-memory session cache with write-behind (backed by FileBackedCache)
# ---------------------------------------------------------------------------

# Use a lambda so that unittest.mock.patch("bot.sessions.SESSION_FILE", ...)
# is picked up at runtime (the lambda reads the module attribute each call).
_cache = FileBackedCache(
    lambda: sys.modules[__name__].SESSION_FILE,  # type: ignore[arg-type]
    flush_interval=5.0,
    mode="debounce",
)


def _reset_cache() -> None:
    """Reset the in-memory session cache. Used by tests."""
    _cache.reset()


def flush_sessions() -> None:
    """Flush cached sessions to disk immediately. Call on clean shutdown."""
    _cache.flush_sync()


def load_sessions() -> dict:
    """Load session mapping (from cache after first call)."""
    return _cache.all()


def save_sessions(sessions: dict) -> None:
    """Persist session mapping (write-behind via cache)."""
    _cache.replace_all(sessions)


def session_key(chat_id: int, thread_id: int, user_id: int) -> str:
    """Build a composite session key: chat_id:thread_id:user_id."""
    return f"{chat_id}:{thread_id}:{user_id}"


def get_session_id(chat_id: int, thread_id: int, user_id: int) -> str | None:
    """Get the Claude session ID for a given chat/thread/user combination."""
    key = session_key(chat_id, thread_id, user_id)
    entry = _cache.get(key)
    if isinstance(entry, dict):
        return entry.get("session_id")
    return None


def set_session_id(chat_id: int, thread_id: int, user_id: int, sid: str) -> None:
    """Store a Claude session ID for a given chat/thread/user combination."""
    key = session_key(chat_id, thread_id, user_id)
    data = _cache.all()
    data.setdefault(key, {})["session_id"] = sid
    data[key]["updated_at"] = datetime.now().isoformat()
    _cache.set(key, data[key])


def clear_session(chat_id: int, thread_id: int, user_id: int) -> None:
    """Clear the session for a chat/thread/user combination, starting fresh."""
    key = session_key(chat_id, thread_id, user_id)
    _cache.delete(key)
    _usage_cache.pop(key, None)


# ---------------------------------------------------------------------------
# Ephemeral usage cache (lost on restart — that's fine)
# ---------------------------------------------------------------------------

_usage_cache: dict[str, dict] = {}


def set_usage(chat_id: int, thread_id: int, user_id: int, data: dict) -> None:
    """Store usage data for a session."""
    _usage_cache[session_key(chat_id, thread_id, user_id)] = data


def get_usage(chat_id: int, thread_id: int, user_id: int) -> dict | None:
    """Get usage data for a session, or None if not available."""
    return _usage_cache.get(session_key(chat_id, thread_id, user_id))


# ---------------------------------------------------------------------------
# Context percentage helper
# ---------------------------------------------------------------------------

_DEFAULT_CONTEXT_WINDOW = 200_000


def get_context_pct(chat_id: int, thread_id: int, user_id: int) -> tuple[float, int, int] | None:
    """Return (percentage, used_tokens, window_size) or None."""
    data = get_usage(chat_id, thread_id, user_id)
    if not data:
        return None
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return None
    used = (
        (usage.get("input_tokens") or 0)
        + (usage.get("cache_creation_input_tokens") or 0)
    )
    if used == 0:
        return None
    window = _DEFAULT_CONTEXT_WINDOW
    pct = used / window if window else 0
    return (pct, used, window)
