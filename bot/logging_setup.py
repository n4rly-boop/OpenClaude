"""Logger setup (infra, workspace loggers)."""

import logging
import logging.handlers

from bot.config import LOGS_DIR, WORKSPACES_DIR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("OpenClaude")

# Structured file logging
LOGS_DIR.mkdir(exist_ok=True)

_LOG_FORMAT = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
)

# Infra logger — startup, shutdown, crashes, ouroboros events
infra_logger = logging.getLogger("OpenClaude.infra")
infra_logger.propagate = False
_infra_handler = logging.handlers.RotatingFileHandler(
    LOGS_DIR / "infra.log", maxBytes=5 * 1024 * 1024, backupCount=3
)
_infra_handler.setFormatter(_LOG_FORMAT)
infra_logger.addHandler(_infra_handler)
infra_logger.setLevel(logging.INFO)

# Workspace logger factory — per-chat activity logs
_workspace_loggers: dict[int, logging.Logger] = {}


def get_workspace_logger(chat_id: int) -> logging.Logger:
    """Return a cached logger that writes to workspaces/c{chat_id}/logs/activity.log."""
    if chat_id in _workspace_loggers:
        return _workspace_loggers[chat_id]
    ws_log_dir = WORKSPACES_DIR / f"c{chat_id}" / "logs"
    ws_log_dir.mkdir(parents=True, exist_ok=True)
    ws_logger = logging.getLogger(f"OpenClaude.ws.{chat_id}")
    ws_logger.propagate = False
    handler = logging.handlers.RotatingFileHandler(
        ws_log_dir / "activity.log", maxBytes=2 * 1024 * 1024, backupCount=2
    )
    handler.setFormatter(_LOG_FORMAT)
    ws_logger.addHandler(handler)
    ws_logger.setLevel(logging.INFO)
    _workspace_loggers[chat_id] = ws_logger
    return ws_logger


def _summarize_input(tool_input: dict) -> str:
    """Truncate a tool input dict to a readable one-liner for log entries."""
    parts = []
    for k, v in tool_input.items():
        v_str = str(v)
        if len(v_str) > 80:
            v_str = v_str[:77] + "..."
        parts.append(f"{k}={v_str}")
    summary = ", ".join(parts)
    return summary[:200] if len(summary) > 200 else summary
