"""OpenClaude slash commands — modular registration."""

from telegram.ext import Application

from .memory import register as register_memory
from .utility import register as register_utility
from .admin import register as register_admin


def register_all(app: Application) -> None:
    """Register all slash-command handlers on the application."""
    register_memory(app)
    register_utility(app)
    register_admin(app)


# Collect every command's (name, description) for /start listing
ALL_COMMANDS: list[tuple[str, str]] = []


def _collect() -> None:
    from .memory import COMMANDS as mem
    from .utility import COMMANDS as util
    from .admin import COMMANDS as adm
    ALL_COMMANDS.clear()
    ALL_COMMANDS.extend(mem)
    ALL_COMMANDS.extend(util)
    ALL_COMMANDS.extend(adm)


_collect()
