"""Streaming backends — SDK and subprocess implementations.

Each backend is an async generator that yields event dicts from the
Claude streaming pipeline.  The shared setup/teardown lives in
``claude.stream_claude()``; backends only contain transport-specific logic.
"""

import asyncio
import contextlib
import json
import logging
import os
import shutil
import time
from collections.abc import AsyncIterator

from bot.config import (
    ALL_TOOLS,
    CLAUDE_TIMEOUT,
    get_claude_model,
)
from bot.formatting import format_tool_status
from bot.logging_setup import _summarize_input
from bot.permissions import build_env, build_sdk_options
from bot.process import _active_procs
from bot.prompts import _append_restart_context, _clear_restart_context, _record_restart_commit
from bot.sdk_session import (
    HAS_SDK,
    AssistantMessage,
    ResultMessage,
    SDKSession,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    _sweep_cancellation_callbacks,
    sdk_session_manager,
)
from bot.sessions import session_key
from bot.streams import set_stream_session_id

logger = logging.getLogger(__name__)

# Re-export for claude.py to check availability
__all__ = ["HAS_SDK", "stream_sdk", "stream_subprocess"]


# ── SDK backend ──────────────────────────────────────────────────────

async def stream_sdk(
    message: str,
    chat_id: int,
    thread_id: int,
    user_id: int,
    is_admin: bool,
    cwd: str,
    sid: str | None,
    verbose: bool,
    stop_event: asyncio.Event | None,
    ws_log,
    *,
    set_session_id_fn,
) -> AsyncIterator[dict]:
    """SDK-based streaming.  Yields event dicts."""
    # We import StreamEvent locally because the name collides with bot.events
    from bot.sdk_session import StreamEvent as SDKStreamEvent

    skey = session_key(chat_id, thread_id, user_id)

    sdk_session = sdk_session_manager.get_or_create(skey, session_id=sid)

    options = build_sdk_options(is_admin, cwd, thread_id, sid, verbose)

    # Connect with exponential backoff (3 retries: 1s, 2s, 4s)
    max_retries = 3
    for attempt in range(max_retries + 1):
        try:
            await sdk_session.ensure_connected(options)
            break
        except Exception as e:
            if attempt == max_retries:
                logger.exception("SDK connect failed after %d retries: %s", max_retries, e)
                msg = f"Failed to connect to Claude after {max_retries} retries: {e}"
                yield {"type": "error", "text": msg}
                return
            delay = 2 ** attempt  # 1s, 2s, 4s
            logger.warning(
                "SDK connect attempt %d failed: %s — retrying in %ds",
                attempt + 1, e, delay,
            )
            with contextlib.suppress(Exception):
                await sdk_session.disconnect()
            sdk_session = SDKSession()
            sdk_session.session_id = sid
            sdk_session_manager.put(skey, sdk_session)
            await asyncio.sleep(delay)

    logger.info("Calling Claude (SDK) for user %d (session: %s)", user_id, sid or "new")
    _record_restart_commit(chat_id)

    result_text = None
    new_session_id = None
    early_session_id = None
    tool_active_count = 0

    try:
        await sdk_session.client.query(message)
        sdk_session.last_activity = time.time()
        sdk_deadline = asyncio.get_event_loop().time() + CLAUDE_TIMEOUT

        async for msg in sdk_session.client.receive_response():
            now = asyncio.get_event_loop().time()
            if stop_event and stop_event.is_set():
                logger.info("Stop event set — aborting SDK stream for user %d", user_id)
                yield {"type": "stopped"}
                return
            # Skip deadline check while a tool is running
            if tool_active_count == 0 and now > sdk_deadline:
                logger.error("SDK stream timed out after %ds for user %d", CLAUDE_TIMEOUT, user_id)
                yield {
                    "type": "error",
                    "text": "Claude took too long to respond. Try again or /new to start fresh.",
                }
                return
            sdk_session.last_activity = time.time()
            sdk_deadline = now + CLAUDE_TIMEOUT
            if msg is None:
                continue

            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, ToolUseBlock):
                        tool_active_count += 1
                        ws_log.info("Tool: %s — %s", block.name, _summarize_input(block.input))
                        status = format_tool_status(block.name, block.input)
                        _append_restart_context(chat_id, f"Tool call [{block.name}]: {status}")
                        yield {"type": "tool_use", "status": status}
                    elif isinstance(block, ToolResultBlock):
                        tool_active_count = max(0, tool_active_count - 1)
                        _append_restart_context(chat_id, "Tool completed")
                        yield {"type": "tool_result"}
                    elif isinstance(block, TextBlock):
                        if block.text:
                            _append_restart_context(chat_id, f"Output started: {block.text[:100]}")
                            yield {"type": "text_block", "text": block.text}

            elif isinstance(msg, SDKStreamEvent):
                if not early_session_id and msg.session_id:
                    early_session_id = msg.session_id
                    set_session_id_fn(chat_id, thread_id, user_id, early_session_id)
                    set_stream_session_id(chat_id, thread_id, user_id, early_session_id)
                    sdk_session.session_id = early_session_id
                    logger.info(
                        "Session ID captured early for user %d: %s",
                        user_id, early_session_id,
                    )
                if verbose:
                    delta = msg.event.get("delta", {})
                    if delta.get("type") == "text_delta":
                        chunk = delta.get("text", "")
                        if chunk:
                            yield {"type": "partial", "text": chunk}

            elif isinstance(msg, ResultMessage):
                _clear_restart_context(chat_id)
                new_session_id = msg.session_id
                result_text = msg.result or ""
                if new_session_id:
                    set_session_id_fn(chat_id, thread_id, user_id, new_session_id)
                    set_stream_session_id(chat_id, thread_id, user_id, new_session_id)
                    sdk_session.session_id = new_session_id
                    logger.info("Session updated for user %d: %s", user_id, new_session_id)
                ws_log.info("Result — session=%s, len=%d", new_session_id, len(result_text))
                yield {"type": "result", "text": result_text, "session_id": new_session_id,
                       "usage": getattr(msg, "usage", None),
                       "cost": getattr(msg, "total_cost_usd", None),
                       "num_turns": getattr(msg, "num_turns", None),
                       "duration_ms": getattr(msg, "duration_ms", None),
                       "duration_api_ms": getattr(msg, "duration_api_ms", None)}

    except Exception as e:
        if stop_event and stop_event.is_set():
            logger.info("SDK stream interrupted by /stop for user %d", user_id)
            yield {"type": "stopped"}
            return
        err_str = str(e)
        if any(s in err_str for s in ("exit code -15", "exit code: -15",
                                       "exit code -9", "exit code: -9")):
            logger.info("SDK process killed by signal (likely bot restart)")
            yield {"type": "silent"}
            return
        logger.exception("SDK streaming error")
        if result_text is None:
            yield {"type": "error", "text": f"Claude error: {e}"}
        return
    finally:
        # Always disconnect the session to prevent zombie subprocesses.
        # We keep the session in the manager (don't pop) so that
        # get_or_create() can see it's disconnected and reuse the slot
        # rather than spawning a duplicate subprocess.
        await sdk_session.disconnect()
        # Safety net: sweep any orphaned _deliver_cancellation callbacks
        # that might have been left behind if the disconnect had to hard-kill.
        _sweep_cancellation_callbacks()

    if result_text is None:
        logger.warning("No result message received from SDK")
        yield {"type": "error", "text": "Claude returned no result."}


# ── Subprocess backend ───────────────────────────────────────────────

async def stream_subprocess(
    message: str,
    chat_id: int,
    thread_id: int,
    user_id: int,
    is_admin: bool,
    cwd: str,
    sid: str | None,
    verbose: bool,
    stop_event: asyncio.Event | None,
    ws_log,
    *,
    set_session_id_fn,
) -> AsyncIterator[dict]:
    """Legacy subprocess-based streaming.  Yields event dicts."""
    claude_bin = shutil.which("claude") or "/root/.local/bin/claude"
    logger.info("Using claude binary: %s (exists: %s)", claude_bin, os.path.isfile(claude_bin))
    cmd = [
        claude_bin,
        "-p", message,
        "--output-format", "stream-json",
        "--verbose",
        "--dangerously-skip-permissions",
        "--allowedTools", ALL_TOOLS,
    ]

    if verbose:
        cmd.append("--include-partial-messages")
    if sid:
        cmd.extend(["--resume", sid])
    model = get_claude_model()
    if model:
        cmd.extend(["--model", model])

    logger.info("Calling Claude (subprocess) for user %d (session: %s)", user_id, sid or "new")
    _record_restart_commit(chat_id)

    env = build_env(is_admin, cwd, thread_id)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        cwd=cwd,
        env=env,
        limit=10 * 1024 * 1024,
    )

    skey = session_key(chat_id, thread_id, user_id)
    _active_procs[skey] = proc

    result_text = None
    new_session_id = None
    deadline = asyncio.get_event_loop().time() + CLAUDE_TIMEOUT

    try:
        while True:
            if stop_event and stop_event.is_set():
                logger.info("Stop event set — killing subprocess for user %d", user_id)
                proc.kill()
                await proc.communicate()
                yield {"type": "stopped"}
                return

            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                proc.kill()
                await proc.communicate()
                logger.error(
                    "Claude CLI timed out after %ds for user %d",
                    CLAUDE_TIMEOUT, user_id,
                )
                yield {
                    "type": "error",
                    "text": "Claude took too long to respond. Try again or /new to start fresh.",
                }
                return

            try:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=min(remaining, 1.0))
            except TimeoutError:
                if stop_event and stop_event.is_set():
                    logger.info(
                        "Stop event set during readline — killing subprocess for user %d",
                        user_id,
                    )
                    proc.kill()
                    await proc.communicate()
                    yield {"type": "stopped"}
                    return
                if remaining <= 1.0:
                    proc.kill()
                    await proc.communicate()
                    logger.error(
                        "Claude CLI timed out after %ds for user %d",
                        CLAUDE_TIMEOUT, user_id,
                    )
                    yield {
                        "type": "error",
                        "text": (
                            "Claude took too long to respond."
                            " Try again or /new to start fresh."
                        ),
                    }
                    return
                continue

            if not line:
                break

            decoded = line.decode().strip()
            if not decoded:
                continue

            try:
                event = json.loads(decoded)
            except json.JSONDecodeError:
                logger.debug("Non-JSON line from Claude: %s", decoded[:200])
                continue

            event_type = event.get("type")

            if event_type == "assistant":
                msg_data = event.get("message", {})
                content = msg_data.get("content", [])
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tool_name = block.get("name", "")
                        tool_input = block.get("input", {})
                        ws_log.info("Tool: %s — %s", tool_name, _summarize_input(tool_input))
                        status = format_tool_status(tool_name, tool_input)
                        _append_restart_context(chat_id, f"Tool call [{tool_name}]: {status}")
                        yield {"type": "tool_use", "status": status}
            elif event_type == "tool_result":
                _append_restart_context(chat_id, "Tool completed")
                yield {"type": "tool_result"}

            elif event_type == "stream_event" and verbose:
                delta = event.get("event", {}).get("delta", {})
                if delta.get("type") == "text_delta":
                    chunk = delta.get("text", "")
                    if chunk:
                        yield {"type": "partial", "text": chunk}

            elif event_type == "result":
                _clear_restart_context(chat_id)
                result_text = event.get("result", "")
                new_session_id = event.get("session_id")
                if new_session_id:
                    set_session_id_fn(chat_id, thread_id, user_id, new_session_id)
                    set_stream_session_id(chat_id, thread_id, user_id, new_session_id)
                    logger.info("Session updated for user %d: %s", user_id, new_session_id)
                ws_log.info("Result — session=%s, len=%d", new_session_id, len(result_text or ""))
                yield {"type": "result", "text": result_text, "session_id": new_session_id,
                       "usage": event.get("usage"),
                       "cost": event.get("total_cost_usd"),
                       "num_turns": event.get("num_turns"),
                       "duration_ms": event.get("duration_ms"),
                       "duration_api_ms": event.get("duration_api_ms")}

        await proc.wait()
        natural_rc = proc.returncode
    finally:
        _active_procs.pop(skey, None)
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()

    if natural_rc != 0:
        if natural_rc < 0:
            sig = -natural_rc
            logger.info("Claude CLI killed by signal %d (likely bot restart)", sig)
            if result_text is None:
                yield {"type": "silent"}
            return
        logger.error("Claude CLI error (rc=%d)", natural_rc)
        ws_log.error("CLI error rc=%d", natural_rc)
        if result_text is None:
            yield {"type": "error", "text": f"Claude CLI error (exit code {natural_rc})"}
        return

    if result_text is None:
        logger.warning("No result event received from stream")
        yield {"type": "error", "text": "Claude returned no result."}
