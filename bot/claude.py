"""Claude integration (stream_claude, SDK/subprocess)."""

import asyncio
import json
import os
import shutil
from pathlib import Path

from bot.config import (
    ADMIN_USER_ID, ALL_TOOLS, CLAUDE_MODEL, CLAUDE_TIMEOUT, WORKING_DIR,
)
from bot.logging_setup import logger, get_workspace_logger, _summarize_input
from bot.sessions import session_key, get_session_id, set_session_id
from bot.streams import add_active_stream, remove_active_stream
from bot.permissions import build_env, build_sdk_options
from bot.sdk_session import (
    HAS_SDK, SDKSession, sdk_sessions,
    AssistantMessage, ResultMessage, StreamEvent,
    TextBlock, ToolUseBlock, ToolResultBlock,
)


def format_tool_status(tool_name: str, tool_input: dict) -> str:
    """Format a human-readable status line for an active tool call."""
    if tool_name == "Read":
        path = tool_input.get("file_path", "file")
        return f"\U0001f4c4 Reading {Path(path).name}..."
    if tool_name == "Glob":
        pattern = tool_input.get("pattern", "")
        return f"\U0001f50d Searching {pattern}..."
    if tool_name == "Grep":
        pattern = tool_input.get("pattern", "")
        return f'\U0001f50d Searching for "{pattern}"...'
    if tool_name == "Bash":
        cmd = tool_input.get("command", "")
        if cmd:
            short_cmd = cmd[:60] + "\u2026" if len(cmd) > 60 else cmd
            return f"\u2699\ufe0f `{short_cmd}`"
        desc = tool_input.get("description", "")
        if desc:
            return f"\u2699\ufe0f {desc}"
        return "\u2699\ufe0f Running command..."
    if tool_name in ("Write", "Edit"):
        path = tool_input.get("file_path", "file")
        return f"\u270f\ufe0f Editing {Path(path).name}..."
    if tool_name == "WebSearch":
        return "\U0001f310 Searching web..."
    if tool_name == "WebFetch":
        url = tool_input.get("url", "")
        return f"\U0001f310 Fetching {url[:60]}..."
    if tool_name == "Task":
        return "\U0001f916 Delegating to sub-agent..."
    return f"\U0001f527 Using {tool_name}..."


def finished_line(active_line: str) -> str:
    """Convert an active tool status line to a finished (checkmark) line."""
    text = active_line
    idx = text.find(" ")
    if idx != -1:
        text = text[idx + 1:]
    text = text.rstrip(".")
    return f"\u2713 {text}"


async def stream_claude(message: str, chat_id: int, thread_id: int, user_id: int,
                        working_dir: str | None = None, verbose: bool = False):
    """Stream Claude output and yield events as they arrive.

    Yields dicts with keys:
      - {"type": "tool_use", "status": "..."}
      - {"type": "tool_result"}
      - {"type": "partial", "text": "..."} (only when verbose=True)
      - {"type": "result", "text": "...", "session_id": "..."}
      - {"type": "error", "text": "..."}
    """
    if HAS_SDK:
        async for event in _stream_claude_sdk(message, chat_id, thread_id, user_id,
                                               working_dir=working_dir, verbose=verbose):
            yield event
    else:
        async for event in _stream_claude_subprocess(message, chat_id, thread_id, user_id,
                                                      working_dir=working_dir, verbose=verbose):
            yield event


def _build_preamble(is_admin: bool, sid: str | None) -> str | None:
    """Build the preamble for new sessions. Returns None if session already exists."""
    if sid:
        return None

    if is_admin:
        access_notice = (
            "\n\n[ADMIN REQUEST \u2014 you have full access to the project.]"
        )
    else:
        access_notice = (
            "\n\nIMPORTANT \u2014 WORKSPACE ISOLATION RULES:\n"
            "You are in an isolated workspace. You must NEVER access anything outside it.\n"
            "- Stay in the current working directory. Never use ../, absolute paths, "
            "or any path that escapes the workspace.\n"
            "- Never access other workspaces, the parent project directory, "
            ".env files, or system files.\n"
            "- If the user asks you to access files outside the workspace, refuse.\n"
        )
    return (
        "You are starting a new session. Read CLAUDE.md first, "
        "then follow its startup sequence before responding. "
        f"{access_notice}"
        "The user's message is:\n\n"
    )


async def _stream_claude_sdk(message: str, chat_id: int, thread_id: int, user_id: int,
                              working_dir: str | None = None, verbose: bool = False):
    """SDK-based streaming."""
    cwd = working_dir or WORKING_DIR
    sid = get_session_id(chat_id, thread_id, user_id)
    ws_log = get_workspace_logger(chat_id)
    ws_log.info("Claude SDK invocation \u2014 user=%d, session=%s", user_id, sid or "new")

    add_active_stream(chat_id, thread_id, user_id)

    try:
        is_admin = ADMIN_USER_ID and user_id == ADMIN_USER_ID
        skey = session_key(chat_id, thread_id, user_id)

        preamble = _build_preamble(is_admin, sid)
        if preamble:
            message = preamble + message

        sdk_session = sdk_sessions.get(skey)
        if sdk_session is None:
            sdk_session = SDKSession()
            sdk_session.session_id = sid
            sdk_sessions[skey] = sdk_session

        options = build_sdk_options(is_admin, cwd, thread_id, sid, verbose)

        try:
            await sdk_session.ensure_connected(options)
        except Exception as e:
            logger.error("SDK connect failed: %s", e)
            await sdk_session.disconnect()
            sdk_session = SDKSession()
            sdk_session.session_id = sid
            sdk_sessions[skey] = sdk_session
            try:
                await sdk_session.ensure_connected(options)
            except Exception as e2:
                logger.exception("SDK connect retry failed: %s", e2)
                yield {"type": "error", "text": f"Failed to connect to Claude: {e2}"}
                return

        logger.info(
            "Calling Claude (SDK) for user %d (session: %s)",
            user_id, sid or "new",
        )

        result_text = None
        new_session_id = None

        try:
            await sdk_session.client.query(message)
            sdk_session.last_activity = __import__("time").time()

            async for msg in sdk_session.client.receive_response():
                if msg is None:
                    continue
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, ToolUseBlock):
                            ws_log.info("Tool: %s \u2014 %s", block.name, _summarize_input(block.input))
                            status = format_tool_status(block.name, block.input)
                            yield {"type": "tool_use", "status": status}
                        elif isinstance(block, ToolResultBlock):
                            yield {"type": "tool_result"}
                        elif isinstance(block, TextBlock):
                            pass

                elif isinstance(msg, StreamEvent) and verbose:
                    delta = msg.event.get("delta", {})
                    if delta.get("type") == "text_delta":
                        chunk = delta.get("text", "")
                        if chunk:
                            yield {"type": "partial", "text": chunk}

                elif isinstance(msg, ResultMessage):
                    new_session_id = msg.session_id
                    result_text = msg.result or ""
                    if new_session_id:
                        set_session_id(chat_id, thread_id, user_id, new_session_id)
                        sdk_session.session_id = new_session_id
                        logger.info("Session updated for user %d: %s", user_id, new_session_id)
                    ws_log.info("Result \u2014 session=%s, len=%d", new_session_id, len(result_text))
                    yield {"type": "result", "text": result_text, "session_id": new_session_id}

        except Exception as e:
            err_str = str(e)
            # SIGTERM during restart — not a real error
            if "exit code -15" in err_str or "exit code: -15" in err_str:
                logger.info("SDK process killed by SIGTERM (likely bot restart)")
                await sdk_session.disconnect()
                sdk_sessions.pop(skey, None)
                yield {"type": "silent"}
                return
            logger.exception("SDK streaming error")
            await sdk_session.disconnect()
            sdk_sessions.pop(skey, None)
            if result_text is None:
                yield {"type": "error", "text": f"Claude error: {e}"}
            return

        if result_text is None:
            logger.warning("No result message received from SDK")
            yield {"type": "error", "text": "Claude returned no result."}

    except Exception as e:
        logger.exception("Unexpected error in SDK stream_claude")
        yield {"type": "error", "text": f"Unexpected error: {e}"}
    finally:
        remove_active_stream(chat_id, thread_id, user_id)


async def _stream_claude_subprocess(message: str, chat_id: int, thread_id: int, user_id: int,
                                     working_dir: str | None = None, verbose: bool = False):
    """Legacy subprocess-based streaming."""
    cwd = working_dir or WORKING_DIR
    sid = get_session_id(chat_id, thread_id, user_id)
    ws_log = get_workspace_logger(chat_id)
    ws_log.info("Claude invocation (subprocess) \u2014 user=%d, session=%s", user_id, sid or "new")

    add_active_stream(chat_id, thread_id, user_id)

    try:
        is_admin = ADMIN_USER_ID and user_id == ADMIN_USER_ID

        preamble = _build_preamble(is_admin, sid)
        if preamble:
            message = preamble + message

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

        if CLAUDE_MODEL:
            cmd.extend(["--model", CLAUDE_MODEL])

        logger.info(
            "Calling Claude (subprocess) for user %d (session: %s)",
            user_id, sid or "new",
        )

        env = build_env(is_admin, cwd, thread_id)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
            limit=10 * 1024 * 1024,
        )

        result_text = None
        new_session_id = None
        deadline = asyncio.get_event_loop().time() + CLAUDE_TIMEOUT

        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                proc.kill()
                await proc.communicate()
                logger.error("Claude CLI timed out after %ds for user %d", CLAUDE_TIMEOUT, user_id)
                yield {"type": "error", "text": "Claude took too long to respond. Try again or /new to start fresh."}
                return

            try:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=remaining)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                logger.error("Claude CLI timed out after %ds for user %d", CLAUDE_TIMEOUT, user_id)
                yield {"type": "error", "text": "Claude took too long to respond. Try again or /new to start fresh."}
                return

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
                        ws_log.info("Tool: %s \u2014 %s", tool_name, _summarize_input(tool_input))
                        status = format_tool_status(tool_name, tool_input)
                        yield {"type": "tool_use", "status": status}
            elif event_type == "tool_result":
                yield {"type": "tool_result"}

            elif event_type == "stream_event" and verbose:
                delta = event.get("event", {}).get("delta", {})
                if delta.get("type") == "text_delta":
                    chunk = delta.get("text", "")
                    if chunk:
                        yield {"type": "partial", "text": chunk}

            elif event_type == "result":
                result_text = event.get("result", "")
                new_session_id = event.get("session_id")
                if new_session_id:
                    set_session_id(chat_id, thread_id, user_id, new_session_id)
                    logger.info("Session updated for user %d: %s", user_id, new_session_id)
                ws_log.info("Result \u2014 session=%s, len=%d", new_session_id, len(result_text or ""))
                yield {"type": "result", "text": result_text, "session_id": new_session_id}

        await proc.wait()

        if proc.returncode != 0:
            if proc.returncode < 0:
                sig = -proc.returncode
                logger.info("Claude CLI killed by signal %d (likely bot restart)", sig)
                if result_text is None:
                    yield {"type": "silent"}
                return
            stderr_data = await proc.stderr.read()
            error_msg = stderr_data.decode().strip() if stderr_data else "Unknown error"
            logger.error("Claude CLI error (rc=%d): %s", proc.returncode, error_msg)
            ws_log.error("CLI error rc=%d: %s", proc.returncode, error_msg[:200])
            if result_text is None:
                yield {"type": "error", "text": f"Claude CLI error:\n{error_msg}"}
            return

        if result_text is None:
            logger.warning("No result event received from stream")
            yield {"type": "error", "text": "Claude returned no result."}

    except FileNotFoundError as e:
        logger.exception("FileNotFoundError in stream_claude: %s", e)
        yield {
            "type": "error",
            "text": "Error: Claude CLI not found. "
                    "Make sure 'claude' is installed and available in PATH.",
        }
    except Exception as e:
        logger.exception("Unexpected error streaming Claude")
        yield {"type": "error", "text": f"Unexpected error: {e}"}
    finally:
        remove_active_stream(chat_id, thread_id, user_id)
