"""
index.py — what has been uploaded, in SQLite on the VM's disk.

`files` has one row per uploaded key; `runs` one row per (PC, prefix) with the
latest run.json. The index is a cache of the store: `rebuild()` recreates it
from the local copies, so losing the database loses nothing.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (key TEXT PRIMARY KEY, pc TEXT NOT NULL, size INTEGER NOT NULL,
                                  sha256 TEXT, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS runs (pc TEXT NOT NULL, prefix TEXT NOT NULL, status TEXT NOT NULL,
                                 updated REAL NOT NULL, PRIMARY KEY (pc, prefix));
"""


class Index:
    def __init__(self, path: str):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript(SCHEMA)

    def put_file(self, key: str, pc: str, size: int, sha256: str | None) -> None:
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?,?)",
                            (key, pc, size, sha256, time.time()))

    def put_run(self, pc: str, prefix: str, status: dict) -> None:
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO runs VALUES (?,?,?,?)",
                            (pc, prefix, json.dumps(status), time.time()))

    def runs(self) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT * FROM runs ORDER BY pc, updated DESC").fetchall()
        return [dict(pc=r["pc"], prefix=r["prefix"], updated=r["updated"], status=json.loads(r["status"]))
                for r in rows]

    def run(self, pc: str, prefix: str) -> dict | None:
        with self.lock:
            r = self.db.execute("SELECT * FROM runs WHERE pc=? AND prefix=?", (pc, prefix)).fetchone()
        return dict(pc=r["pc"], prefix=r["prefix"], updated=r["updated"],
                    status=json.loads(r["status"])) if r else None

    def files(self, pc: str | None = None, like: str | None = None) -> list[dict]:
        sql, args = "SELECT * FROM files WHERE 1=1", []
        if pc:
            sql += " AND pc=?"
            args.append(pc)
        if like:
            sql += " AND key LIKE ?"
            args.append(like)
        with self.lock:
            return [dict(r) for r in self.db.execute(sql + " ORDER BY key", args).fetchall()]

    def rebuild(self, store_root: str) -> int:
        """Recreate the index from the local copies (run.json files and sizes)."""
        n = 0
        for dirpath, _dirs, names in os.walk(store_root):
            for name in names:
                path = os.path.join(dirpath, name)
                key = os.path.relpath(path, store_root).replace(os.sep, "/")
                self.put_file(key, key.split("/", 1)[0], os.path.getsize(path), None)
                n += 1
                if name == "run.json":
                    try:
                        with open(path, encoding="utf-8") as fh:
                            status = json.load(fh)
                        parts = key.split("/")
                        self.put_run(parts[0], "/".join(parts[1:-1]), status)
                    except (OSError, json.JSONDecodeError):
                        pass
        return n
