"""SDKSession class, idle cleanup, shutdown."""

import asyncio
import time

from bot.config import SDK_IDLE_TIMEOUT
from bot.logging_setup import logger

# Claude Code SDK — persistent session support
try:
    from claude_code_sdk import (
        ClaudeSDKClient,
        ClaudeCodeOptions,
        AssistantMessage,
        ResultMessage,
        TextBlock,
        ToolUseBlock,
        ToolResultBlock,
        PermissionResultAllow,
        PermissionResultDeny,
    )
    from claude_code_sdk.types import StreamEvent

    # Patch SDK to skip unknown message types (e.g. rate_limit_event)
    import claude_code_sdk._internal.message_parser as _mp
    _original_parse = _mp.parse_message
    def _patched_parse(data):
        try:
            return _original_parse(data)
        except _mp.MessageParseError:
            return None
    _mp.parse_message = _patched_parse
    import claude_code_sdk._internal.client as _cl
    _cl.parse_message = _patched_parse

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

# Global dict: session_key -> SDKSession
sdk_sessions: dict[str, "SDKSession"] = {}


class SDKSession:
    """Wraps a ClaudeSDKClient with lifecycle management."""

    def __init__(self):
        self.client = None
        self.session_id: str | None = None
        self.last_activity: float = time.time()
        self.lock: asyncio.Lock = asyncio.Lock()
        self.connected: bool = False

    async def ensure_connected(self, options) -> None:
        """Connect the SDK client if not already connected."""
        if self.connected and self.client:
            return
        self.client = ClaudeSDKClient(options=options)
        await self.client.connect()
        self.connected = True
        self.last_activity = time.time()

    async def disconnect(self) -> None:
        """Disconnect the SDK client."""
        if self.client:
            try:
                await self.client.disconnect()
            except Exception as e:
                logger.debug("SDKSession disconnect error: %s", e)
            finally:
                self.client = None
                self.connected = False


async def cleanup_idle_sessions():
    """Periodic task to disconnect idle SDK sessions."""
    while True:
        await asyncio.sleep(60)
        now = time.time()
        expired = [k for k, s in sdk_sessions.items()
                   if now - s.last_activity > SDK_IDLE_TIMEOUT]
        for key in expired:
            session = sdk_sessions.pop(key)
            logger.info("Disconnecting idle SDK session: %s", key)
            await session.disconnect()


async def shutdown_sdk_sessions():
    """Disconnect all SDK sessions (called on bot shutdown)."""
    for key, session in list(sdk_sessions.items()):
        logger.info("Shutting down SDK session: %s", key)
        await session.disconnect()
    sdk_sessions.clear()
