"""
Telethon client wrapper with file lock, backoff, and error handling.

Usage:
    async with TGClient() as client:
        me = await client.get_me()
"""

import fcntl
import json
import os
import sys
import time
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import (
    AuthKeyUnregisteredError,
    FloodWaitError,
    SessionPasswordNeededError,
    UserDeactivatedBanError,
)


def _workspace_dir() -> str:
    d = os.environ.get("OPENCLAUDE_WORKSPACE_DIR", "")
    if not d:
        print(
            json.dumps({"error": "config", "message": "OPENCLAUDE_WORKSPACE_DIR not set"}),
            file=sys.stderr,
        )
        sys.exit(1)
    return d


def _error_exit(error_type: str, message: str, code: int = 1):
    print(json.dumps({"error": error_type, "message": message}), file=sys.stderr)
    sys.exit(code)


class TGClient:
    """Async context manager wrapping Telethon with file lock and error handling."""

    def __init__(self):
        self._workspace = _workspace_dir()
        self._api_id = os.environ.get("TG_MONITOR_API_ID", "")
        self._api_hash = os.environ.get("TG_MONITOR_API_HASH", "")
        self._phone = os.environ.get("TG_MONITOR_PHONE", "")

        if not self._api_id or not self._api_hash or not self._phone:
            _error_exit(
                "config",
                "Missing TG_MONITOR_API_ID, TG_MONITOR_API_HASH, or TG_MONITOR_PHONE",
            )

        self._session_path = self._resolve_session()
        self._lock_path = Path(self._workspace) / "tg-client.lock"
        self._lock_fd = None
        self._client = None

    def _resolve_session(self) -> str:
        """Try tg-client-session first, fall back to lead-monitor-session."""
        ws = Path(self._workspace)
        primary = ws / "tg-client-session"
        fallback = ws / "lead-monitor-session"

        # If primary session file exists, use it
        if primary.with_suffix(".session").exists():
            return str(primary)
        # If fallback exists and primary doesn't, use fallback
        if fallback.with_suffix(".session").exists():
            return str(fallback)
        # Default to primary (will be created on first auth)
        return str(primary)

    def _acquire_lock(self, timeout: float = 30.0):
        """Acquire file lock with timeout."""
        self._lock_fd = open(self._lock_path, "w")
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except (IOError, OSError):
                if time.monotonic() >= deadline:
                    self._lock_fd.close()
                    self._lock_fd = None
                    _error_exit("lock_timeout", "Could not acquire tg-client.lock within 30s")
                time.sleep(0.5)

    def _release_lock(self):
        if self._lock_fd:
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                self._lock_fd.close()
            except Exception:
                pass
            self._lock_fd = None

    async def __aenter__(self) -> TelegramClient:
        self._acquire_lock()
        try:
            self._client = TelegramClient(
                self._session_path, int(self._api_id), self._api_hash
            )
            await self._client.connect()

            if not await self._client.is_user_authorized():
                self._release_lock()
                _error_exit("not_authorized", "Session not authorized. Run interactive auth first.", code=3)

            return self._client
        except FloodWaitError as e:
            self._release_lock()
            print(
                json.dumps({"error": "flood_wait", "wait_seconds": e.seconds}),
                file=sys.stderr,
            )
            sys.exit(2)
        except (AuthKeyUnregisteredError, UserDeactivatedBanError) as e:
            self._release_lock()
            _error_exit("not_authorized", str(e), code=3)
        except SessionPasswordNeededError:
            self._release_lock()
            _error_exit("not_authorized", "2FA password required. Run interactive auth first.", code=3)
        except Exception as e:
            self._release_lock()
            _error_exit("connection", str(e))

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._client:
            try:
                await self._client.disconnect()
            except Exception:
                pass
        self._release_lock()

        # Handle FloodWaitError that occurred during command execution
        if exc_type is FloodWaitError and exc_val:
            print(
                json.dumps({"error": "flood_wait", "wait_seconds": exc_val.seconds}),
                file=sys.stderr,
            )
            sys.exit(2)
        return False
