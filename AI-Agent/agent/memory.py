from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional


class SQLiteMemory:
    def __init__(self, db_path: str):
        self.db_path = os.path.abspath(db_path)
        parent = os.path.dirname(self.db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_memory_items_key ON memory_items(key)
                """
            )

    def put(self, key: str, value: Any) -> None:
        payload = json.dumps(value, ensure_ascii=False)
        import time

        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO memory_items(key, value, created_at) VALUES(?,?,?)",
                (key, payload, int(time.time())),
            )

    def get(self, key: str) -> Optional[Any]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM memory_items WHERE key = ? ORDER BY id DESC LIMIT 1",
                (key,),
            ).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row["value"])
        except (TypeError, json.JSONDecodeError):
            return row["value"]

    def list(self) -> Dict[str, Any]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT key, value FROM memory_items ORDER BY id DESC LIMIT 200"
            ).fetchall()
        out: Dict[str, Any] = {}
        for r in rows:
            try:
                out[r["key"]] = json.loads(r["value"])
            except Exception:
                out[r["key"]] = r["value"]
        return out

    def delete_key(self, key: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM memory_items WHERE key = ?", (key,))

    def forget_all(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM memory_items")

