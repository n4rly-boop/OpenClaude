"""Tests for active stream tracking."""

from pathlib import Path
from unittest.mock import patch

from bot.streams import (
    add_active_stream, remove_active_stream,
    load_active_streams, save_active_streams,
)


class TestActiveStreams:
    def test_add_and_remove(self, tmp_dir):
        sf = tmp_dir / "streams.json"
        with patch("bot.streams.ACTIVE_STREAMS_FILE", sf):
            add_active_stream(1, 0, 99)
            streams = load_active_streams()
            assert "1:0:99" in streams
            assert streams["1:0:99"]["chat_id"] == 1

            remove_active_stream(1, 0, 99)
            assert load_active_streams() == {}

    def test_load_missing_file(self, tmp_dir):
        sf = tmp_dir / "nonexistent.json"
        with patch("bot.streams.ACTIVE_STREAMS_FILE", sf):
            assert load_active_streams() == {}

    def test_file_deleted_when_empty(self, tmp_dir):
        sf = tmp_dir / "streams.json"
        with patch("bot.streams.ACTIVE_STREAMS_FILE", sf):
            add_active_stream(1, 0, 99)
            assert sf.exists()
            remove_active_stream(1, 0, 99)
            assert not sf.exists()

    def test_multiple_streams(self, tmp_dir):
        sf = tmp_dir / "streams.json"
        with patch("bot.streams.ACTIVE_STREAMS_FILE", sf):
            add_active_stream(1, 0, 99)
            add_active_stream(2, 0, 88)
            streams = load_active_streams()
            assert len(streams) == 2
            remove_active_stream(1, 0, 99)
            streams = load_active_streams()
            assert len(streams) == 1
            assert "2:0:88" in streams
