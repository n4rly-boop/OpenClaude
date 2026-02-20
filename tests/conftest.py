"""Shared fixtures for OpenClaude tests."""

import os
import pytest


@pytest.fixture(autouse=True)
def dummy_env(monkeypatch, tmp_path):
    """Set required env vars so bot.config can load without a real .env."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "0000000000:AAFakeTokenForTesting")
    monkeypatch.setenv("ALLOWED_USERS", "111111,222222")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "111111")
    monkeypatch.setenv("WORKING_DIR", str(tmp_path))
    yield


@pytest.fixture
def tmp_dir(tmp_path):
    """Provide a temporary directory for file-based tests."""
    return tmp_path
