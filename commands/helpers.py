"""Shared references to telegram-bot.py internals.

telegram-bot.py has a hyphen in its name, so it can't be imported directly.
This module provides a late-binding interface: telegram-bot.py calls
helpers.init(globals()) at startup to inject the references we need.
"""

# These are populated by init() at bot startup
is_authorized = None
get_thread_id = None
ensure_workspace = None
split_message = None
load_sessions = None
_load_active_streams = None
_run_with_streaming = None

# Config values
ADMIN_USER_ID = None
CLAUDE_MODEL = None
SCRIPT_DIR = None
WORKSPACES_DIR = None
LOGS_DIR = None
ALLOWED_USERS = None
logger = None
infra_logger = None


def init(bot_globals: dict) -> None:
    """Called by telegram-bot.py to inject its functions and config."""
    global is_authorized, get_thread_id, ensure_workspace, split_message
    global load_sessions, _load_active_streams, _run_with_streaming
    global ADMIN_USER_ID, CLAUDE_MODEL, SCRIPT_DIR, WORKSPACES_DIR, LOGS_DIR
    global ALLOWED_USERS, logger, infra_logger

    is_authorized = bot_globals["is_authorized"]
    get_thread_id = bot_globals["get_thread_id"]
    ensure_workspace = bot_globals["ensure_workspace"]
    split_message = bot_globals["split_message"]
    load_sessions = bot_globals["load_sessions"]
    _load_active_streams = bot_globals["_load_active_streams"]
    _run_with_streaming = bot_globals["_run_with_streaming"]

    ADMIN_USER_ID = bot_globals["ADMIN_USER_ID"]
    CLAUDE_MODEL = bot_globals.get("CLAUDE_MODEL", "")
    SCRIPT_DIR = bot_globals["SCRIPT_DIR"]
    WORKSPACES_DIR = bot_globals["WORKSPACES_DIR"]
    LOGS_DIR = bot_globals["LOGS_DIR"]
    ALLOWED_USERS = bot_globals["ALLOWED_USERS"]
    logger = bot_globals["logger"]
    infra_logger = bot_globals["infra_logger"]


def get_claude_model() -> str:
    """Get current CLAUDE_MODEL (reads live from the module that set it)."""
    return CLAUDE_MODEL or ""


def set_claude_model(model: str) -> None:
    """Update CLAUDE_MODEL. This updates our reference but also needs
    the caller to update the original module variable."""
    global CLAUDE_MODEL
    CLAUDE_MODEL = model
