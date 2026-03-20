"""Restart recovery — resume interrupted sessions after bot restart."""

import asyncio
import contextlib
import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from bot.config import ACTIVE_STREAMS_FILE, RESTART_MESSAGES_FILE, RESTART_STATE_FILE
from bot.logging_setup import infra_logger
from bot.prompts import _get_current_commit, _read_restart_commit, _read_restart_context
from bot.renderer import TelegramRenderer
from bot.rollback import mark_consumed as mark_rollback_consumed
from bot.sessions import get_session_id, session_key
from bot.workspaces import get_working_dir

logger = logging.getLogger(__name__)

RESUME_TIMEOUT = 120  # seconds — kill resume if it takes longer
_RESTART_INDICATOR = "\u267b\ufe0f \u041f\u0435\u0440\u0435\u0437\u0430\u043f\u0443\u0441\u043a..."


class RestartRecoveryService:
    """Handles resuming interrupted sessions after a bot restart."""

    def __init__(
        self,
        bot: Any,
        renderer: TelegramRenderer,
        stream_fn: Any,
        settings: dict[str, Any] | None = None,
    ) -> None:
        self._bot = bot
        self._renderer = renderer
        self._stream_fn = stream_fn  # bot.claude.stream_claude
        self._settings = settings or {}

    async def recover_interrupted_sessions(self) -> None:
        """Edit restart messages and resume all interrupted generations."""
        await self._edit_restart_messages()
        interrupted = self._collect_interrupted()
        if not interrupted:
            infra_logger.info("No interrupted sessions to resume")
            return

        infra_logger.info(
            "Resuming %d interrupted generation(s): %s",
            len(interrupted),
            list(interrupted.keys()),
        )

        async def _run_resumes() -> None:
            # Small delay to let the event loop and bot fully initialize
            await asyncio.sleep(2)
            try:
                await asyncio.gather(
                    *[self._resume_with_timeout(e) for e in interrupted.values()]
                )
                infra_logger.info("Restart recovery complete")
            except Exception:
                infra_logger.exception("Restart recovery task failed")

        asyncio.create_task(_run_resumes())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _edit_restart_messages(self) -> None:
        """Edit 'Restarting...' messages to show success."""
        if not RESTART_MESSAGES_FILE.exists():
            return
        try:
            msgs = json.loads(RESTART_MESSAGES_FILE.read_text())
            for entry in msgs:
                try:
                    await self._bot.edit_message_text(
                        chat_id=entry["chat_id"],
                        message_id=entry["message_id"],
                        text="\u2705 Restart complete",
                    )
                except Exception as e:
                    infra_logger.warning(
                        "Failed to edit restart message %s in chat %s: %s",
                        entry.get("message_id"),
                        entry.get("chat_id"),
                        e,
                    )
        except (json.JSONDecodeError, OSError) as e:
            infra_logger.warning("Failed to read restart messages file: %s", e)
        finally:
            RESTART_MESSAGES_FILE.unlink(missing_ok=True)

    @staticmethod
    def _collect_interrupted() -> dict[str, dict]:
        """Collect interrupted chats from restart state and active streams files."""
        interrupted: dict[str, dict] = {}
        for state_file in (RESTART_STATE_FILE, ACTIVE_STREAMS_FILE):
            if not state_file.exists():
                infra_logger.info("State file not found: %s", state_file)
                continue
            try:
                raw = state_file.read_text()
                data = json.loads(raw)
                infra_logger.info(
                    "Loaded %d entries from %s", len(data), state_file.name,
                )
                interrupted.update(data)
            except (json.JSONDecodeError, OSError) as e:
                infra_logger.warning(
                    "Failed to read state file %s: %s", state_file, e,
                )
            finally:
                state_file.unlink(missing_ok=True)
        return interrupted

    async def _resume_chat(self, entry: dict) -> None:
        """Resume a single interrupted chat session."""
        from bot.chat_lock import get_chat_lock, release_chat_lock_ref, set_lock_holder

        cid = entry["chat_id"]
        tid = entry["thread_id"]
        uid = entry["user_id"]
        # Group chats use uid=0 for shared sessions (same as handlers.py)
        session_uid = 0 if cid < 0 else uid
        tg_thread_id = tid or None
        live_message_id: int | None = entry.get("live_message_id")
        status_msg_id: int | None = entry.get("status_msg_id")

        # Acquire chat lock to prevent concurrent streaming
        skey_for_lock = session_key(cid, tid, session_uid)
        lock = get_chat_lock(skey_for_lock)
        try:
            await asyncio.wait_for(lock.acquire(), timeout=10)
        except TimeoutError:
            infra_logger.warning(
                "Resume skipped for chat=%d thread=%d user=%d — lock busy",
                cid, tid, uid,
            )
            release_chat_lock_ref(skey_for_lock)
            return
        set_lock_holder(skey_for_lock)

        # Delete the stuck live message immediately (same as normal flow after generation)
        if live_message_id:
            with contextlib.suppress(Exception):
                await self._bot.delete_message(chat_id=cid, message_id=live_message_id)

        # Delete orphaned tool-status message from previous generation
        if status_msg_id:
            with contextlib.suppress(Exception):
                await self._bot.delete_message(chat_id=cid, message_id=status_msg_id)

        try:  # noqa: SIM105 — outer try wraps the full resume with lock release in finally
            session_id = get_session_id(cid, tid, session_uid)
            if not session_id:
                # Fallback: session_id stored directly in the stream entry
                session_id = entry.get("session_id")
                if session_id:
                    infra_logger.info(
                        "Session recovered from stream entry for chat=%d thread=%d user=%d: %s",
                        cid, tid, uid, session_id,
                    )
                    # Persist it to sessions cache so stream_claude can find it
                    from bot.sessions import set_session_id as _set_sid

                    _set_sid(cid, tid, session_uid, session_id)
            if not session_id:
                infra_logger.warning(
                    "No session for chat=%d thread=%d user=%d, skipping resume",
                    cid, tid, uid,
                )
                # Still notify the user
                with contextlib.suppress(Exception):
                    await self._bot.send_message(
                        chat_id=cid,
                        text=(
                            "\u26a0\ufe0f Bot restarted \u2014 no session to resume."
                            " Send a new message to continue."
                        ),
                        message_thread_id=tg_thread_id,
                    )
                return

            resume_msg = self._build_resume_message(cid, entry)
            chat_working_dir = get_working_dir(cid)
            stop_event = asyncio.Event()
            infra_logger.info(
                "Starting resume stream for chat=%d thread=%d user=%d session=%s",
                cid, tid, uid, session_id,
            )

            # Use StreamingSession for full streaming UX (typing, tool status, live text)
            from bot.streaming_session import StreamingSession

            session = StreamingSession(
                bot=self._bot,
                chat_id=cid,
                thread_id=tid,
                tg_thread_id=tg_thread_id,
                streaming=True,
                show_tools=True,
            )
            session.session_user_id = session_uid

            # Send typing indicator
            typing_task = asyncio.create_task(
                self._typing_loop(cid, tg_thread_id)
            )

            try:
                async for event in self._stream_fn(
                    resume_msg,
                    cid,
                    tid,
                    session_uid,
                    working_dir=chat_working_dir,
                    stop_event=stop_event,
                    real_user_id=uid,
                ):
                    etype = event.get("type")

                    # Stop typing once visible output is streaming
                    if etype == "partial" and typing_task and not typing_task.done():
                        typing_task.cancel()

                    if etype == "error":
                        infra_logger.warning(
                            "Resume stream error for chat=%d: %s",
                            cid, event.get("text", ""),
                        )

                    await session.handle_event(event)

                # Final flush of any buffered live text
                await session.flush_live()
            finally:
                if typing_task and not typing_task.done():
                    typing_task.cancel()
                    # Allow the cancellation to propagate cleanly before
                    # awaiting cleanup_status — prevents CancelledError
                    # from leaking into the delete() call.
                    with contextlib.suppress(asyncio.CancelledError):
                        await typing_task
                infra_logger.info(
                    "_resume_chat: calling cleanup_status, status_msg=%s",
                    session.status_msg.message_id if session.status_msg else None,
                )
                await session.cleanup_status()

            if session.stopped:
                return

            # Finalize: send final response, files, images
            if session.response_text or session.live_text:
                await session.finalize_response()
            else:
                infra_logger.warning(
                    "Resume produced no output for chat=%d thread=%d user=%d",
                    cid, tid, uid,
                )
                # Still notify the user that recovery happened
                with contextlib.suppress(Exception):
                    await self._bot.send_message(
                        chat_id=cid,
                        text=(
                            "\u2705 Bot restarted successfully."
                            " Send a message to continue."
                        ),
                        message_thread_id=tg_thread_id,
                    )

            # Delete the old stuck live message now that recovery is complete
            if live_message_id:
                with contextlib.suppress(Exception):
                    await self._bot.delete_message(
                        chat_id=cid, message_id=live_message_id,
                    )

            # Mark this session as having received rollback info via restart recovery
            skey = session_key(cid, tid, session_uid)
            mark_rollback_consumed(skey)

            infra_logger.info("Resumed chat=%d thread=%d user=%d", cid, tid, uid)
        except Exception as e:
            infra_logger.error(
                "Failed to resume chat=%d thread=%d user=%d: %s", cid, tid, uid, e
            )
            with contextlib.suppress(Exception):
                await self._bot.send_message(
                    chat_id=cid,
                    text=(
                        "\u26a0\ufe0f Bot restarted \u2014 couldn't resume your"
                        " previous task. Send a new message to continue."
                    ),
                    message_thread_id=tg_thread_id,
                )
        finally:
            with contextlib.suppress(RuntimeError):
                lock.release()
            release_chat_lock_ref(skey_for_lock)

    @staticmethod
    def _is_rollback(old_commit: str, current_commit: str) -> bool:
        """Return True if current_commit is OLDER than old_commit (actual rollback).

        Uses ``git merge-base --is-ancestor`` to check whether the current
        commit is an ancestor of the old commit, which means we went backward.
        """
        project_dir = str(Path(__file__).resolve().parent.parent)
        try:
            result = subprocess.run(
                ["git", "-C", project_dir, "merge-base", "--is-ancestor",
                 current_commit, old_commit],
                capture_output=True, timeout=5,
            )
            # Exit 0 means current_commit IS an ancestor of old_commit → rollback
            return result.returncode == 0
        except Exception:
            return False  # Can't determine, assume not a rollback

    @staticmethod
    def _build_git_state(cid: int) -> tuple[str, bool]:
        """Build a git state line for the resume message.

        Returns (state_string, is_rollback).
        """
        old_commit = _read_restart_commit(cid)
        current_commit = _get_current_commit()
        if not old_commit or old_commit == "unknown":
            return f"Git state: now on {current_commit}.", False
        if old_commit == current_commit:
            return f"Git state: commit {current_commit} (unchanged).", False
        if RestartRecoveryService._is_rollback(old_commit, current_commit):
            return (
                f"Git state: was on {old_commit}, now on {current_commit} "
                "(ROLLBACK OCCURRED — a bad commit was reverted by safe-restart).",
                True,
            )
        return (
            f"Git state: was on {old_commit}, now on {current_commit} "
            f"(updated to {current_commit}).",
            False,
        )

    @staticmethod
    def _build_resume_message(cid: int, entry: dict) -> str:
        """Build the system message used to prompt Claude to resume."""
        git_state, is_rollback = RestartRecoveryService._build_git_state(cid)
        restart_ctx = _read_restart_context(cid)

        if restart_ctx:
            if is_rollback:
                return (
                    "[System: The bot restarted mid-generation.\n\n"
                    f"{git_state}\n\n"
                    f"What you were doing:\n{restart_ctx}\n\n"
                    "The restart was triggered by the safe-restart hook "
                    "which rolled back a bad commit.\n\n"
                    "Tell the user: bot restarted, a rollback occurred "
                    "(explain which commit was reverted), "
                    "and what you were doing. Then continue the task.]"
                )
            return (
                "[System: The bot restarted mid-generation.\n\n"
                f"{git_state}\n\n"
                f"What you were doing:\n{restart_ctx}\n\n"
                "Tell the user the bot restarted and briefly what you were "
                "doing, then continue the task.]"
            )
        user_msg_hint = entry.get("user_message", "")
        if user_msg_hint:
            return (
                "[System: The bot restarted mid-generation.\n\n"
                f"{git_state}\n\n"
                f'The user\'s message was: "{user_msg_hint}".\n\n'
                "Briefly notify the user that you restarted and are continuing, "
                "then immediately resume and complete the task without waiting "
                "for confirmation.]"
            )
        return (
            "[System: The bot restarted mid-generation.\n\n"
            f"{git_state}\n\n"
            "Briefly notify the user that you restarted and "
            "are continuing, then resume the task without waiting for "
            "confirmation.]"
        )

    async def _typing_loop(self, chat_id: int, thread_id: int | None) -> None:
        """Send 'typing' chat action periodically until cancelled."""
        try:
            while True:
                try:
                    await self._bot.send_chat_action(
                        chat_id=chat_id,
                        action="typing",
                        message_thread_id=thread_id if thread_id else None,
                    )
                except Exception:
                    infra_logger.debug(
                        "typing indicator send failed for chat %d", chat_id,
                    )
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            pass

    async def _resume_with_timeout(self, entry: dict) -> None:
        """Resume a chat with a timeout guard."""
        try:
            await asyncio.wait_for(
                self._resume_chat(entry), timeout=RESUME_TIMEOUT
            )
        except (TimeoutError, asyncio.CancelledError):
            cid = entry["chat_id"]
            tid = entry["thread_id"]
            uid = entry["user_id"]
            session_uid = 0 if cid < 0 else uid
            infra_logger.error(
                "Resume timed out after %ds for chat=%d thread=%d user=%d",
                RESUME_TIMEOUT, cid, tid, uid,
            )
            # Hard-kill the SDK session to prevent orphaned subprocesses
            # and break anyio's _deliver_cancellation busy-loop.
            from bot.sdk_session import sdk_session_manager
            skey = session_key(cid, tid, session_uid)
            sdk_sess = sdk_session_manager.get(skey)
            if sdk_sess:
                sdk_sess.hard_kill()
                sdk_sess.client = None
                sdk_sess.connected = False
            with contextlib.suppress(Exception):
                await self._bot.send_message(
                    chat_id=cid,
                    text=(
                        "\u26a0\ufe0f Bot restarted \u2014 resume timed out."
                        " Send a new message to continue."
                    ),
                    message_thread_id=tid or None,
                )
