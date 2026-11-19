"""A tiny SQLite key-value cache with TTL, shared by search and fetch tools.

Research runs repeat the same searches while you iterate on prompts; caching makes
re-runs fast, cheap and reproducible.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


class Cache:
    def __init__(self, path: Path | str | None, ttl_s: int = 7 * 24 * 3600):
        self.ttl_s = ttl_s
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path) if path else ":memory:", check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT, t REAL)")
        self.hits = self.misses = 0

    @staticmethod
    def key(*parts: Any) -> str:
        return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()

    def get(self, k: str) -> Any | None:
        with self._lock:
            row = self._db.execute("SELECT v, t FROM kv WHERE k=?", (k,)).fetchone()
        if row and time.time() - row[1] < self.ttl_s:
            self.hits += 1
            return json.loads(row[0])
        self.misses += 1
        return None

    def set(self, k: str, v: Any) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO kv VALUES (?,?,?)", (k, json.dumps(v), time.time()))
            self._db.commit()
