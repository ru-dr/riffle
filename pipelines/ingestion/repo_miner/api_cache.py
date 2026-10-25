"""
api_cache.py — a small on-disk cache of what the miner reads from GitHub per PR, so a restarted
PC does not read it again.

One SQLite file per repo beside the other ci_cache files (`<owner>_<name>.api.sqlite`). A value is
the JSON the API call returned (compressed), keyed by what was asked (`tl:<pr>` timeline,
`rv:<pr>:<bots>` review activity, `cc:<pr>` commit count). Only complete answers are stored: an
unreadable or truncated read is never cached, so a gap is retried, not remembered.

A cached answer is what the first read saw, the same way the pinned PR selection is. Entries
older than MAX_AGE_S are ignored, so a long-lived cache cannot hide new events for ever. The timeline of a closed
or merged PR is kept CLOSED_KEEP_S (90 days): it costs one GitHub call of a 5,000 an hour quota to read again.
RIFFLE_NO_API_CACHE=1 turns it off. Every failure here falls back to reading GitHub.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import zlib

MAX_AGE_S = 14 * 86400
CLOSED_KEEP_S = 90 * 86400      # a closed or merged PR's timeline is final bar late comments: kept this long (see put)


class ApiCache:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._db: sqlite3.Connection | None = None
        self._dead = False

    def _conn(self) -> sqlite3.Connection | None:
        if self._dead:
            return None
        if self._db is None:
            try:
                os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
                db = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, t REAL, v BLOB)")
                db.execute("DELETE FROM kv WHERE t < ?", (time.time() - MAX_AGE_S,))      # rows past their age are never read again
                db.commit()
                self._db = db
            except (sqlite3.Error, OSError):
                self._dead = True
                return None
        return self._db

    def get(self, key: str):
        """The stored value, or None when missing, old or unreadable."""
        with self._lock:
            db = self._conn()
            if db is None:
                return None
            try:
                row = db.execute("SELECT t, v FROM kv WHERE k = ?", (key,)).fetchone()
                if not row or time.time() - row[0] > MAX_AGE_S:
                    return None
                return json.loads(zlib.decompress(row[1]))
            except (sqlite3.Error, zlib.error, ValueError):
                return None

    def put(self, key: str, value, ttl: float | None = None, keep: float | None = None) -> None:
        """Store `value`. `ttl` (seconds, under MAX_AGE_S): an answer that is only good for a while, such
        as the timeline of a PR still open; it is stored as if it were that much older, so it expires
        (and is purged) sooner. `keep` (seconds, over MAX_AGE_S): an answer good for longer, stored as if
        it were newer."""
        t = time.time() - (MAX_AGE_S - ttl if ttl is not None and ttl < MAX_AGE_S else 0)
        if keep is not None and keep > MAX_AGE_S:
            t = time.time() + (keep - MAX_AGE_S)
        with self._lock:
            db = self._conn()
            if db is None:
                return
            try:
                db.execute("INSERT OR REPLACE INTO kv (k, t, v) VALUES (?, ?, ?)",
                           (key, t, zlib.compress(json.dumps(value).encode())))
                db.commit()
            except (sqlite3.Error, TypeError, ValueError):
                pass


def open_for(ci_cache_dir: str, owner: str, repo: str) -> ApiCache | None:
    if os.environ.get("RIFFLE_NO_API_CACHE"):
        return None
    return ApiCache(os.path.join(ci_cache_dir, f"{owner}_{repo}.api.sqlite"))
