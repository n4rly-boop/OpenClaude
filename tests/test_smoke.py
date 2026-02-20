"""Smoke tests — catch syntax errors, bad imports, broken module structure."""

import importlib


def test_import_bot_config():
    importlib.import_module("bot.config")


def test_import_bot_logging():
    importlib.import_module("bot.logging_setup")


def test_import_bot_sessions():
    importlib.import_module("bot.sessions")


def test_import_bot_streams():
    importlib.import_module("bot.streams")


def test_import_bot_workspaces():
    importlib.import_module("bot.workspaces")


def test_import_bot_renderer():
    importlib.import_module("bot.renderer")


def test_import_bot_permissions():
    importlib.import_module("bot.permissions")


def test_import_bot_sdk_session():
    importlib.import_module("bot.sdk_session")


def test_import_bot_claude():
    importlib.import_module("bot.claude")


def test_import_bot_handlers():
    importlib.import_module("bot.handlers")


def test_import_bot_app():
    importlib.import_module("bot.app")


def test_import_commands_config():
    importlib.import_module("commands.config")


def test_import_commands_admin():
    importlib.import_module("commands.admin")


def test_import_commands_memory():
    importlib.import_module("commands.memory")


def test_import_commands_utility():
    importlib.import_module("commands.utility")


def test_app_builds():
    """The Telegram Application object should build with dummy env vars."""
    from telegram.ext import Application
    from bot.config import TELEGRAM_BOT_TOKEN
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    assert app is not None
