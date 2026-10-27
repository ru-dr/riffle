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
CREATE TABLE IF NOT EXISTS activity (pc TEXT NOT NULL, prefix TEXT NOT NULL, ts REAL NOT NULL, reason TEXT,
                                     prs_done INTEGER, repos_done INTEGER, current TEXT);
CREATE INDEX IF NOT EXISTS activity_ts ON activity (ts);
CREATE TABLE IF NOT EXISTS loki_offsets (key TEXT PRIMARY KEY, offset INTEGER NOT NULL);
"""
ACTIVITY_KEEP_S = 35 * 86400                                 # the activity graph looks back at most 30 days


def like_escape(text: str) -> str:
    """`text` as a literal in a LIKE pattern used with ESCAPE '\\' (names contain `_`, a wildcard)."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class Index:
    def __init__(self, path: str):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript(SCHEMA)

    def put_file(self, key: str, pc: str, size: int, sha256: str | None, updated: float | None = None) -> None:
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?,?)",
                            (key, pc, size, sha256, time.time() if updated is None else updated))

    def loki_offset(self, key: str) -> int:
        """How many bytes of this trace Loki is known to have (loki.py)."""
        with self.lock:
            r = self.db.execute("SELECT offset FROM loki_offsets WHERE key=?", (key,)).fetchone()
        return int(r[0]) if r else 0

    def set_loki_offset(self, key: str, offset: int) -> None:
        with self.lock, self.db:
            self.db.execute("INSERT INTO loki_offsets VALUES (?,?) ON CONFLICT(key) DO UPDATE SET offset=MAX(offset, excluded.offset)",
                            (key, offset))

    def reset_loki_offset(self, key: str) -> None:
        """Forget how much of a trace Loki has (the file was replaced by a shorter one)."""
        with self.lock, self.db:
            self.db.execute("DELETE FROM loki_offsets WHERE key=?", (key,))

    def put_run(self, pc: str, prefix: str, status: dict, updated: float | None = None) -> None:
        """`updated` is when the status was really written; a rebuild passes the file's own time, so a PC that crashed
        long ago is not shown as live, and no activity point is made for it (nothing happened now)."""
        now = time.time() if updated is None else updated
        num = lambda v: v if isinstance(v, int) and not isinstance(v, bool) else None   # noqa: E731
        cur = status.get("current")
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO runs VALUES (?,?,?,?)", (pc, prefix, json.dumps(status), now))
            if updated is not None:
                return
            # every status push is one point on the activity timeline (a heartbeat while mining, a push per repo)
            self.db.execute("INSERT INTO activity VALUES (?,?,?,?,?,?,?)",
                            (pc, prefix, now, str(status.get("reason"))[:40] if status.get("reason") is not None else None,
                             num(status.get("prs_done")), num(status.get("repos_done")), cur[:120] if isinstance(cur, str) else None))
            self.db.execute("DELETE FROM activity WHERE ts < ?", (now - ACTIVITY_KEEP_S,))

    def activity(self, since: float) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT * FROM activity WHERE ts >= ? ORDER BY ts", (since,)).fetchall()
        return [dict(r) for r in rows]

    def runs(self) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT * FROM runs ORDER BY pc, updated DESC").fetchall()
        return [dict(pc=r["pc"], prefix=r["prefix"], updated=r["updated"], status=json.loads(r["status"]))
                for r in rows]

    def latest_runs(self) -> list[dict]:
        """One run per PC: the one uploaded last. A PC that restarted on a newer miner version
        has a run under each version's prefix, but it is still one PC; `versions` lists the
        prefixes it has (newest first), so the older runs stay reachable."""
        by_pc: dict[str, list[dict]] = {}
        for r in self.runs():
            by_pc.setdefault(r["pc"], []).append(r)
        out = []
        for pc in sorted(by_pc):
            rs = sorted(by_pc[pc], key=lambda r: r["updated"], reverse=True)
            out.append(dict(rs[0], versions=[r["prefix"] for r in rs]))
        return out

    def run(self, pc: str, prefix: str) -> dict | None:
        with self.lock:
            r = self.db.execute("SELECT * FROM runs WHERE pc=? AND prefix=?", (pc, prefix)).fetchone()
        return dict(pc=r["pc"], prefix=r["prefix"], updated=r["updated"],
                    status=json.loads(r["status"])) if r else None

    def file(self, key: str) -> dict | None:
        """One file by its exact key (`files(like=)` treats _ and % as wildcards)."""
        with self.lock:
            r = self.db.execute("SELECT * FROM files WHERE key=?", (key,)).fetchone()
        return dict(r) if r else None

    def files(self, pc: str | None = None, like: str | None = None, limit: int | None = None) -> list[dict]:
        sql, args = "SELECT * FROM files WHERE 1=1", []
        if pc:
            sql += " AND pc=?"
            args.append(pc)
        if like:
            sql += " AND key LIKE ? ESCAPE '\\'"
            args.append(like)
        sql += " ORDER BY key" + (f" LIMIT {int(limit)}" if limit else "")
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def rebuild(self, store_root: str, bucket_files=()) -> int:
        """Recreate the index from the local copies (run.json files and sizes). `bucket_files` is
        (key, size, updated) for what the bucket holds: the big files that have no local copy are indexed too."""
        n = 0
        seen = set()
        for dirpath, _dirs, names in os.walk(store_root):
            for name in names:
                path = os.path.join(dirpath, name)
                key = os.path.relpath(path, store_root).replace(os.sep, "/")
                mtime = os.path.getmtime(path)
                self.put_file(key, key.split("/", 1)[0], os.path.getsize(path), None, mtime)
                seen.add(key)
                n += 1
                if name == "run.json":
                    try:
                        with open(path, encoding="utf-8") as fh:
                            status = json.load(fh)
                        parts = key.split("/")
                        if isinstance(status, dict):
                            self.put_run(parts[0], "/".join(parts[1:-1]), status, mtime)
                    except (OSError, json.JSONDecodeError):
                        pass
        for key, size, updated in bucket_files:
            if key not in seen and not key.startswith("_system/") and key.count("/") >= 2:
                self.put_file(key, key.split("/", 1)[0], size or 0, None, updated)
                n += 1
        return n
