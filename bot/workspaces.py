"""Per-chat workspace creation, symlinks, memory."""

import os
import shutil
from pathlib import Path

from bot.config import WORKSPACES_DIR, WORKING_DIR
from bot.logging_setup import logger

# Shared files are symlinked into each workspace so updates propagate automatically
_SYMLINKED_FILES = ["TOOLS.md", "CLAUDE.md"]
_SYMLINKED_DIRS = [".claude"]
# BOOTSTRAP.md is always freshly copied so new sessions run the first-run ritual
_BOOTSTRAP_FILE = "BOOTSTRAP.md"


def ensure_workspace(chat_id: int) -> Path:
    """Create and return an isolated workspace directory for the given chat.

    Workspace layout:
      workspaces/c{chat_id}/
        TOOLS.md       -> symlink to ../../TOOLS.md
        CLAUDE.md      -> symlink to ../../CLAUDE.md
        .claude/       -> symlink to ../../.claude
        SOUL.md        <- independent copy (set up via BOOTSTRAP.md)
        IDENTITY.md    <- independent copy (set up via BOOTSTRAP.md)
        USER.md        <- independent copy
        BOOTSTRAP.md   <- fresh copy every new session
        memory/        <- isolated per-chat memory
          MEMORY.md
    """
    workspace = WORKSPACES_DIR / f"c{chat_id}"
    if workspace.exists():
        _sync_workspace_links(workspace)
        return workspace

    workspace.mkdir(parents=True, exist_ok=True)
    base = Path(WORKING_DIR)

    # Symlink shared files
    for fname in _SYMLINKED_FILES:
        src = base / fname
        dst = workspace / fname
        if src.exists() and not dst.exists():
            dst.symlink_to(os.path.relpath(src, workspace))

    # Symlink shared directories
    for dname in _SYMLINKED_DIRS:
        src = base / dname
        dst = workspace / dname
        if src.exists() and not dst.exists():
            dst.symlink_to(os.path.relpath(src, workspace))

    # Always copy BOOTSTRAP.md fresh so new sessions run the first-run ritual
    bootstrap = base / _BOOTSTRAP_FILE
    if bootstrap.exists():
        shutil.copy2(bootstrap, workspace / _BOOTSTRAP_FILE)

    # Create isolated memory directory
    mem_dir = workspace / "memory"
    mem_dir.mkdir(exist_ok=True)
    mem_template = base / "memory" / "MEMORY.md"
    mem_dst = mem_dir / "MEMORY.md"
    if mem_template.exists() and not mem_dst.exists():
        shutil.copy2(mem_template, mem_dst)

    logger.info("Created workspace for chat %d at %s", chat_id, workspace)
    return workspace


def _sync_workspace_links(workspace: Path) -> None:
    """Ensure symlinks in an existing workspace point to current shared files."""
    base = Path(WORKING_DIR)
    for fname in _SYMLINKED_FILES:
        src = base / fname
        dst = workspace / fname
        if src.exists() and not dst.exists():
            dst.symlink_to(os.path.relpath(src, workspace))
    for dname in _SYMLINKED_DIRS:
        src = base / dname
        dst = workspace / dname
        if src.exists() and not dst.exists():
            dst.symlink_to(os.path.relpath(src, workspace))


def get_working_dir(chat_id: int) -> str:
    """Return the working directory for a given chat."""
    return str(ensure_workspace(chat_id))
