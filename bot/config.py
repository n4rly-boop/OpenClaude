"""Configuration loading (.env, constants, authorization)."""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the package's parent directory (OpenClaude/)
SCRIPT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(SCRIPT_DIR / ".env")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ALLOWED_USERS_RAW = os.getenv("ALLOWED_USERS", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "")
WORKING_DIR = os.getenv("WORKING_DIR") or str(SCRIPT_DIR)

# Workspaces directory for per-chat isolation
WORKSPACES_DIR = SCRIPT_DIR / "workspaces"

# Parse allowed users (first entry is admin)
ALLOWED_USERS: set[int] = set()
ALLOWED_USERS_LIST: list[int] = []
if ALLOWED_USERS_RAW.strip():
    for uid in ALLOWED_USERS_RAW.split(","):
        uid = uid.strip()
        if uid.isdigit():
            ALLOWED_USERS.add(int(uid))
            ALLOWED_USERS_LIST.append(int(uid))

ADMIN_USER_ID: int | None = ALLOWED_USERS_LIST[0] if ALLOWED_USERS_LIST else None

# Session file
SESSION_FILE = Path.home() / ".openclaude-sessions.json"

# Claude CLI allowed tools
ALL_TOOLS = "Read,Write,Edit,Bash,Glob,Grep,WebFetch,WebSearch,Task,Skill"

# Telegram message limit
TELEGRAM_MAX_LENGTH = 4096

# Claude CLI timeout (seconds)
CLAUDE_TIMEOUT = 300

# Active stream tracking
ACTIVE_STREAMS_FILE = SCRIPT_DIR / ".active-streams.json"

# Restart state
RESTART_STATE_FILE = SCRIPT_DIR / ".restart-state.json"

# Restart notification messages (for editing after outcome)
RESTART_MESSAGES_FILE = SCRIPT_DIR / ".restart-messages.json"

# Minimum interval between Telegram message edits (seconds)
STATUS_EDIT_INTERVAL = 1.5

# Batch window: messages arriving within this many seconds are combined
BATCH_WINDOW = 1.5

# SDK idle timeout (seconds)
SDK_IDLE_TIMEOUT = 300

# Logs directory
LOGS_DIR = SCRIPT_DIR / "logs"


def get_claude_model() -> str:
    """Get current CLAUDE_MODEL."""
    return CLAUDE_MODEL or ""


def set_claude_model(model: str) -> None:
    """Update CLAUDE_MODEL at runtime."""
    global CLAUDE_MODEL
    CLAUDE_MODEL = model


def is_authorized(user_id: int) -> bool:
    """Check if a user is authorized to use the bot."""
    if not ALLOWED_USERS:
        return False
    return user_id in ALLOWED_USERS


def get_thread_id(update) -> int:
    """Get the forum topic thread ID, or 0 for non-forum messages."""
    msg = update.message
    return msg.message_thread_id if msg and msg.message_thread_id else 0
