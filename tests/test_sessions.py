"""Tests for session persistence."""

import json
from pathlib import Path
from unittest.mock import patch

from bot.sessions import (
    session_key, load_sessions, save_sessions,
    get_session_id, set_session_id, clear_session,
)


def test_session_key_format():
    assert session_key(123, 456, 789) == "123:456:789"


def test_session_key_zero_thread():
    assert session_key(123, 0, 789) == "123:0:789"


class TestSaveLoadRoundtrip:
    def test_roundtrip(self, tmp_dir):
        sf = tmp_dir / "sessions.json"
        with patch("bot.sessions.SESSION_FILE", sf):
            data = {"123:0:789": {"session_id": "abc123"}}
            save_sessions(data)
            loaded = load_sessions()
            assert loaded == data

    def test_missing_file_returns_empty(self, tmp_dir):
        sf = tmp_dir / "nonexistent.json"
        with patch("bot.sessions.SESSION_FILE", sf):
            assert load_sessions() == {}

    def test_corrupt_json_returns_empty(self, tmp_dir):
        sf = tmp_dir / "bad.json"
        sf.write_text("{invalid json!!")
        with patch("bot.sessions.SESSION_FILE", sf):
            assert load_sessions() == {}


class TestSessionLifecycle:
    def test_set_get_clear(self, tmp_dir):
        sf = tmp_dir / "sessions.json"
        with patch("bot.sessions.SESSION_FILE", sf):
            set_session_id(1, 0, 99, "sess-abc")
            assert get_session_id(1, 0, 99) == "sess-abc"
            clear_session(1, 0, 99)
            assert get_session_id(1, 0, 99) is None
