"""
SQLite state and audit database for tg-client.

Database location: $OPENCLAUDE_WORKSPACE_DIR/tg-client.db
"""

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


def _get_db_path() -> str:
    ws = os.environ.get("OPENCLAUDE_WORKSPACE_DIR", "")
    if not ws:
        print(
            json.dumps({"error": "config", "message": "OPENCLAUDE_WORKSPACE_DIR not set"}),
            file=sys.stderr,
        )
        sys.exit(1)
    return str(Path(ws) / "tg-client.db")


class StateDB:
    """SQLite database for tracking state and audit log."""

    def __init__(self):
        self.conn = sqlite3.connect(_get_db_path())
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init_tables()

    def _init_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS state (
                chat_id TEXT PRIMARY KEY,
                last_message_id INTEGER,
                last_run TEXT
            );
            CREATE TABLE IF NOT EXISTS audit_log (
                ts TEXT,
                action TEXT,
                chat_id TEXT,
                data TEXT
            );
        """)
        self.conn.commit()

    def get_last_message_id(self, chat_id) -> int:
        cur = self.conn.execute(
            "SELECT last_message_id FROM state WHERE chat_id = ?",
            (str(chat_id),),
        )
        row = cur.fetchone()
        return row[0] if row and row[0] else 0

    def set_last_message_id(self, chat_id, msg_id: int):
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """INSERT INTO state (chat_id, last_message_id, last_run)
               VALUES (?, ?, ?)
               ON CONFLICT(chat_id)
               DO UPDATE SET last_message_id = MAX(last_message_id, excluded.last_message_id),
                             last_run = excluded.last_run""",
            (str(chat_id), msg_id, now),
        )
        self.conn.commit()

    def log_action(self, action: str, chat_id, data=None):
        now = datetime.now(timezone.utc).isoformat()
        data_str = json.dumps(data, ensure_ascii=False) if data else None
        self.conn.execute(
            "INSERT INTO audit_log (ts, action, chat_id, data) VALUES (?, ?, ?, ?)",
            (now, action, str(chat_id) if chat_id else None, data_str),
        )
        self.conn.commit()

    def close(self):
        self.conn.close()
