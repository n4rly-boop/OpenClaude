"""SDKSession class, idle cleanup, shutdown."""

import asyncio
import contextlib
import logging
import os
import signal
import time

from bot.config import SDK_IDLE_TIMEOUT

logger = logging.getLogger(__name__)

# Claude Code SDK — persistent session support
try:
    from claude_code_sdk import (
        AssistantMessage,
        ClaudeCodeOptions,
        ClaudeSDKClient,
        PermissionResultAllow,
        PermissionResultDeny,
        ResultMessage,
        TextBlock,
        ToolResultBlock,
        ToolUseBlock,
    )
    from claude_code_sdk.types import StreamEvent

    HAS_SDK = True
except ImportError:
    HAS_SDK = False
    # Provide stub names so type hints don't crash
    ClaudeSDKClient = None
    ClaudeCodeOptions = None
    AssistantMessage = None
    ResultMessage = None
    TextBlock = None
    ToolUseBlock = None
    ToolResultBlock = None
    PermissionResultAllow = None
    PermissionResultDeny = None
    StreamEvent = None

def _apply_sdk_compat_patch() -> None:
    """Apply compatibility patch for claude-code-sdk <= 0.0.25.

    The SDK's ``parse_message()`` raises ``MessageParseError`` for any
    unknown message type (e.g. ``rate_limit_event``).  This crashes the
    streaming loop.  The patch catches the exception and returns ``None``
    instead, which the bot's stream handler already skips (``if msg is
    None: continue``).

    Remove this patch when the SDK handles unknown message types
    gracefully (returns ``None`` or a sentinel) in a future release.
    """
    if not HAS_SDK:
        return

    import claude_code_sdk

    def _safe_version_part(s: str) -> int:
        try:
            return int(s.split("-")[0].split("+")[0])
        except (ValueError, IndexError):
            return 0

    sdk_version = tuple(
        _safe_version_part(x) for x in claude_code_sdk.__version__.split(".")[:3]
    )
    # TODO: update the upper bound when a fixed SDK version is released
    if sdk_version > (0, 0, 25):
        logger.info(
            "SDK version %s detected — skipping parse_message compat patch "
            "(verify unknown-type handling before removing this check)",
            claude_code_sdk.__version__,
        )
        return

    import claude_code_sdk._internal.client as _cl
    import claude_code_sdk._internal.message_parser as _mp

    _original_parse = _mp.parse_message

    def _patched_parse(data):  # noqa: ANN001
        try:
            return _original_parse(data)
        except _mp.MessageParseError:
            logger.debug(
                "Skipping unknown SDK message: %s",
                data.get("type") if isinstance(data, dict) else data,
            )
            return None

    _mp.parse_message = _patched_parse
    _cl.parse_message = _patched_parse
    logger.debug(
        "Applied SDK parse_message compat patch for version %s",
        claude_code_sdk.__version__,
    )


_apply_sdk_compat_patch()


def _apply_sdk_setsid_patch() -> None:
    """Patch the SDK transport to spawn Claude in its own process group.

    Without this, the SDK subprocess inherits the bot's pgid, making it
    impossible to kill via ``os.killpg()`` without also killing the bot.
    The patch injects ``start_new_session=True`` into the
    ``anyio.open_process()`` call so the subprocess gets its own pgid.
    """
    if not HAS_SDK:
        return

    try:
        import anyio as _anyio

        _original_open_process = _anyio.open_process

        async def _patched_open_process(*args, **kwargs):
            kwargs.setdefault("start_new_session", True)
            return await _original_open_process(*args, **kwargs)

        _anyio.open_process = _patched_open_process
        logger.debug("Applied SDK setsid patch — subprocesses will get their own pgid")
    except Exception:
        logger.warning("Failed to apply SDK setsid patch", exc_info=True)


_apply_sdk_setsid_patch()

DISCONNECT_TIMEOUT = 3  # seconds


def _sweep_cancellation_callbacks() -> None:
    """Remove orphaned anyio _deliver_cancellation callbacks from the event loop.

    When a subprocess is killed (SIGKILL) before anyio can cleanly shut down its
    CancelScopes, the ``_deliver_cancellation`` callback reschedules itself via
    ``call_soon()`` in an infinite busy loop, burning 100% CPU.

    The loop continues because ``scope._tasks`` references orphaned tasks that
    will never complete, so ``should_retry`` is always True.

    Fix: cancel the handle, clear ``scope._tasks`` so retry stops, and set
    ``scope._cancel_handle = None`` so the scope thinks delivery is done.
    """
    try:
        loop = asyncio.get_running_loop()
        removed = 0
        for attr in ('_ready', '_scheduled'):
            queue = getattr(loop, attr, None)
            if queue is None:
                continue
            for handle in list(queue):
                if handle.cancelled():
                    continue
                cb = getattr(handle, '_callback', None)
                if cb is None:
                    continue
                cb_name = getattr(cb, '__name__', '')
                if cb_name == '_deliver_cancellation':
                    handle.cancel()
                    removed += 1
                    # Neutralize the scope so it can never re-schedule:
                    # clear _tasks (the retry condition) and _cancel_handle.
                    scope = getattr(cb, '__self__', None)
                    if scope is not None:
                        with contextlib.suppress(AttributeError, TypeError):
                            scope._tasks.clear()
                        with contextlib.suppress(AttributeError, TypeError):
                            scope._cancel_handle = None
        if removed:
            logger.warning("Swept %d orphaned _deliver_cancellation callbacks", removed)
    except Exception:
        logger.debug("Failed to sweep cancellation callbacks", exc_info=True)

# Cache the bot's own process group so we never kill ourselves
_BOT_PGID = os.getpgid(os.getpid())


def _get_descendants(pid: int) -> list[int]:
    """Get all descendant PIDs of a process recursively."""
    result = []
    try:
        with open(f"/proc/{pid}/task/{pid}/children") as f:
            child_pids = [int(p) for p in f.read().split()]
        for cpid in child_pids:
            result.extend(_get_descendants(cpid))
            result.append(cpid)
    except (FileNotFoundError, ValueError, OSError):
        pass
    return result


def _kill_tree(pid: int) -> None:
    """SIGKILL a process, its process group, and all descendants.

    Claude sub-agents are spawned with ``detached: true`` (own process
    group), so ``killpg`` alone misses them.  We collect the full
    descendant tree *before* killing anything — once the parent dies,
    detached children are re-parented to PID 1 and become invisible
    to ``/proc/<pid>/children``.
    """
    # 1. Snapshot all descendants BEFORE killing (critical — after the
    #    parent dies, detached children vanish from the /proc tree).
    descendants = _get_descendants(pid)

    # 2. Collect distinct process groups among descendants so we can
    #    killpg each detached sub-agent group in one shot.
    descendant_pgids: set[int] = set()
    for cpid in descendants:
        with contextlib.suppress(ProcessLookupError, OSError):
            cpgid = os.getpgid(cpid)
            if cpgid != _BOT_PGID:
                descendant_pgids.add(cpgid)

    # 3. killpg the main process (if it has its own group).
    try:
        pgid = os.getpgid(pid)
        if pgid != _BOT_PGID:
            descendant_pgids.add(pgid)
    except (ProcessLookupError, OSError):
        pass  # process already gone

    killed_via_pgid = 0
    for gpid in descendant_pgids:
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(gpid, signal.SIGKILL)
            killed_via_pgid += 1

    # 4. Kill any stragglers individually (processes that changed pgid,
    #    or whose group kill raced with re-parenting).
    stragglers = 0
    for cpid in descendants:
        try:
            os.kill(cpid, signal.SIGKILL)
            stragglers += 1
        except (ProcessLookupError, OSError):
            pass
    with contextlib.suppress(ProcessLookupError, OSError):
        os.kill(pid, signal.SIGKILL)

    logger.info(
        "Killed process tree pid=%d: %d descendant(s), %d group(s), %d straggler(s)",
        pid, len(descendants), killed_via_pgid, stragglers,
    )


class SDKSession:
    """Wraps a ClaudeSDKClient with lifecycle management."""

    def __init__(self):
        self.client = None
        self.session_id: str | None = None
        self.last_activity: float = time.time()
        self.connected: bool = False

    async def ensure_connected(self, options) -> None:
        """Connect the SDK client if not already connected."""
        if self.connected and self.client:
            return
        self.client = ClaudeSDKClient(options=options)
        try:
            await asyncio.wait_for(self.client.connect(), timeout=30.0)
        except TimeoutError:
            self.hard_kill()
            self.client = None
            self.connected = False
            raise TimeoutError("SDK connect timed out after 30s") from None
        except Exception:
            # connect() may have spawned a subprocess before failing —
            # kill it so it doesn't become an orphan
            self.hard_kill()
            self.client = None
            self.connected = False
            raise
        self.connected = True
        self.last_activity = time.time()

    def _get_subprocess_pid(self) -> int | None:
        """Extract the PID of the underlying Claude Code subprocess."""
        try:
            return self.client._query.transport._process.pid
        except (AttributeError, TypeError):
            return None

    def hard_kill(self) -> None:
        """Kill the subprocess and all its descendants immediately via SIGKILL.

        Session state is already saved on disk after ResultMessage, so
        SIGKILL is safe at this point.  Skipping SIGTERM avoids a blocking
        2-second sleep that stalls the event loop.

        After killing, sweeps orphaned anyio ``_deliver_cancellation``
        callbacks from the event loop to prevent infinite busy loops.
        """
        pid = self._get_subprocess_pid()
        if not pid:
            return
        _kill_tree(pid)
        _sweep_cancellation_callbacks()

    async def disconnect(self) -> None:
        """Disconnect the SDK client (with timeout to prevent hangs).

        On clean disconnect: only kill detached sub-agent children (own pgid).
        On failed disconnect (timeout/error): hard-kill the full process tree.

        Using SIGKILL after a clean disconnect breaks anyio's CancelScope
        cleanup, causing _deliver_cancellation to busy-loop at 100% CPU.
        """
        if self.client:
            # Capture PID and descendant info before disconnect — the
            # subprocess reference may be cleared during SDK disconnect().
            pid = self._get_subprocess_pid()
            # Snapshot detached sub-agent pgids BEFORE disconnect.
            # After the main process dies, children are re-parented to PID 1.
            detached_pgids: set[int] = set()
            if pid:
                for cpid in _get_descendants(pid):
                    with contextlib.suppress(ProcessLookupError, OSError):
                        cpgid = os.getpgid(cpid)
                        # Detached sub-agents have their own pgid (different
                        # from the main process and the bot).
                        if cpgid != _BOT_PGID:
                            try:
                                main_pgid = os.getpgid(pid)
                            except (ProcessLookupError, OSError):
                                main_pgid = None
                            if cpgid != main_pgid:
                                detached_pgids.add(cpgid)

            clean = False
            try:
                await asyncio.wait_for(self.client.disconnect(), timeout=DISCONNECT_TIMEOUT)
                clean = True
            except TimeoutError:
                logger.warning(
                    "SDKSession disconnect timed out after %ds",
                    DISCONNECT_TIMEOUT,
                )
            except Exception as e:
                logger.warning("SDKSession disconnect error: %s", e)
            finally:
                if clean:
                    # Clean disconnect — only kill detached sub-agent groups.
                    # Do NOT kill the main process tree (anyio cleaned up).
                    for gpid in detached_pgids:
                        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                            os.killpg(gpid, signal.SIGKILL)
                    if detached_pgids:
                        logger.info(
                            "Killed %d detached sub-agent group(s) after clean disconnect",
                            len(detached_pgids),
                        )
                else:
                    # Failed disconnect — hard-kill everything.
                    if pid:
                        _kill_tree(pid)

                self.client = None
                self.connected = False

                # Schedule multiple sweeps to catch _deliver_cancellation
                # callbacks that appear after I/O events are processed.
                # The subprocess death notification arrives asynchronously,
                # so a single immediate sweep misses them.
                _sweep_cancellation_callbacks()
                try:
                    _loop = asyncio.get_running_loop()
                    for delay in (0.05, 0.2, 0.5, 1.0, 3.0):
                        _loop.call_later(delay, _sweep_cancellation_callbacks)
                except RuntimeError:
                    pass


class SDKSessionManager:
    """Manages the lifecycle of all SDKSession instances.

    Replaces the former bare ``sdk_sessions`` dict with explicit methods for
    creation, retrieval, disconnection, idle cleanup, and shutdown.
    """

    def __init__(self, idle_timeout: int = SDK_IDLE_TIMEOUT):
        self._sessions: dict[str, SDKSession] = {}
        self._idle_timeout = idle_timeout

    # ── dict-like helpers (backward compat) ──────────────────────────

    def get(self, key: str) -> SDKSession | None:
        """Return the session for *key*, or ``None``."""
        return self._sessions.get(key)

    def pop(self, key: str) -> SDKSession | None:
        """Remove and return the session for *key*, or ``None``."""
        return self._sessions.pop(key, None)

    def __contains__(self, key: str) -> bool:
        return key in self._sessions

    def __len__(self) -> int:
        return len(self._sessions)

    # ── core API ─────────────────────────────────────────────────────

    def get_or_create(self, key: str, session_id: str | None = None) -> SDKSession:
        """Return an existing session or create a new one for *key*.

        If an existing session is found but is still connected (orphaned
        from a previous call), hard-kill its subprocess before returning
        a fresh session.  This prevents duplicate Claude processes.
        """
        session = self._sessions.get(key)
        if session is not None:
            if session.connected and session.client:
                logger.warning(
                    "get_or_create(%s): existing session still connected — "
                    "hard-killing orphaned subprocess before creating new one",
                    key,
                )
                session.hard_kill()
                session.client = None
                session.connected = False
            return session
        session = SDKSession()
        session.session_id = session_id
        self._sessions[key] = session
        return session

    def put(self, key: str, session: SDKSession) -> None:
        """Store *session* under *key*.

        If there is an existing connected session under this key,
        hard-kill it first to prevent orphaned subprocesses.
        """
        old = self._sessions.get(key)
        if old is not None and old is not session and old.connected:
            logger.warning(
                "put(%s): replacing still-connected session — hard-killing old subprocess",
                key,
            )
            old.hard_kill()
            old.client = None
            old.connected = False
        self._sessions[key] = session

    async def disconnect(self, key: str) -> None:
        """Disconnect and remove the session identified by *key*."""
        session = self._sessions.pop(key, None)
        if session:
            logger.info("Disconnecting SDK session: %s", key)
            await session.disconnect()

    async def cleanup_idle(self) -> None:
        """Periodic task — disconnect sessions idle longer than the timeout."""
        from bot.streams import load_active_streams
        while True:
            try:
                await asyncio.sleep(60)
                now = time.time()
                active = load_active_streams()
                expired = [
                    k for k, s in self._sessions.items()
                    if now - s.last_activity > self._idle_timeout
                    and k not in active
                ]
                for key in expired:
                    session = self._sessions.pop(key, None)
                    if session:
                        logger.info("Disconnecting idle SDK session: %s", key)
                        await session.disconnect()
            except Exception:
                logger.exception("Error in SDKSessionManager.cleanup_idle loop")

    def get_active_pids(self) -> set[int]:
        """Return the set of subprocess PIDs for all connected sessions."""
        pids: set[int] = set()
        for session in self._sessions.values():
            if session.connected and session.client:
                pid = session._get_subprocess_pid()
                if pid:
                    pids.add(pid)
        return pids

    async def shutdown_all(self) -> None:
        """Disconnect every session (called on bot shutdown)."""
        tasks = []
        for key, session in list(self._sessions.items()):
            logger.info("Shutting down SDK session: %s", key)
            tasks.append(session.disconnect())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._sessions.clear()


# ── Singleton instance ───────────────────────────────────────────────
sdk_session_manager = SDKSessionManager()

# Backward-compatible alias so existing ``from bot.sdk_session import sdk_sessions``
# still works.  The manager exposes .get / .pop / __contains__ / __len__ but
# callers should migrate to the manager's explicit API.
sdk_sessions = sdk_session_manager


# ── Legacy function shims (thin wrappers around the manager) ─────────

async def cleanup_idle_sessions() -> None:
    """Start the idle-cleanup loop (legacy entry-point)."""
    await sdk_session_manager.cleanup_idle()


async def shutdown_sdk_sessions() -> None:
    """Disconnect all sessions (legacy entry-point)."""
    await sdk_session_manager.shutdown_all()
