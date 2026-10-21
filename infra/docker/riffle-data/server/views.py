"""
views.py — what the file viewer reads: parquet through DuckDB, JSON, JSONL, text
logs and run traces. Every function takes local paths (store.fetch gets them) and
returns plain data for the pages' JSON endpoints.

DuckDB runs in memory, limited to the cache and store folders, with every other
file operation switched off. Column names that reach SQL come from the file's own
schema, never from the request; values are bound as parameters.
"""
from __future__ import annotations

import collections
import datetime as dt
import decimal
import json
import os
import re

MAX_ROWS = 500
MAX_SCAN_BYTES = 300 * 1024 ** 2
MAX_JSON_BYTES = 8 * 1024 ** 2
TIME_COLUMNS = ("created_at", "merged_at", "closed_at", "pr_created_at", "committed_at", "mined_at")


def kind_of(key: str) -> str:
    name = key.rsplit("/", 1)[-1]
    if name.endswith(".parquet"):
        return "parquet"
    if name.endswith(".trace.jsonl"):
        return "trace"
    if name.endswith(".jsonl"):
        return "jsonl"
    if name.endswith(".json"):
        return "json"
    if name.endswith((".log", ".txt", ".md", ".csv")):
        return "text"
    return "binary"


def jsonable(v):
    """A value the browser can show: dates as ISO text, nested data as short JSON text."""
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return None if v != v else v
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray)):
        return "0x" + bytes(v[:16]).hex() + ("…" if len(v) > 16 else "")
    try:
        s = json.dumps(v, default=str)
    except (TypeError, ValueError):
        s = str(v)
    return s if len(s) <= 300 else s[:299] + "…"


# -- parquet -----------------------------------------------------------------

class Parquet:
    """One or more parquet files (the same table from several PCs) as one relation."""

    def __init__(self, paths: list[str], allowed_dirs: list[str]):
        import duckdb
        self.paths = paths
        self.con = duckdb.connect(":memory:")
        self.con.execute("SET memory_limit='1GB'")
        self.con.execute("SET threads=2")
        dirs = ", ".join("'" + d.replace("'", "''") + "'" for d in allowed_dirs)
        self.con.execute(f"SET allowed_directories=[{dirs}]")
        self.con.execute("SET enable_external_access=false")
        listing = ", ".join("'" + p.replace("'", "''") + "'" for p in paths)
        self.rel = f"read_parquet([{listing}], union_by_name=true, filename=true)"
        desc = self.con.execute(f"DESCRIBE SELECT * FROM {self.rel}").fetchall()
        self.columns = [(r[0], r[1]) for r in desc]
        self.types = dict(self.columns)

    @staticmethod
    def q(name: str) -> str:
        return '"' + name.replace('"', '""') + '"'

    def count(self) -> int:
        return self.con.execute(f"SELECT count(*) FROM {self.rel}").fetchone()[0]

    def schema(self) -> dict:
        """Row count plus one entry per column: type, null share, distinct (approx), min, max."""
        total = self.count()
        cols = []
        for r in self.con.execute(f"SUMMARIZE SELECT * FROM {self.rel}").fetchall():
            name, typ, mn, mx, uniq, avg, _sd, _q25, q50, _q75, cnt, nullp = r[:12]
            cols.append({"name": name, "type": typ, "min": jsonable(mn), "max": jsonable(mx),
                         "distinct": uniq, "avg": jsonable(avg), "median": jsonable(q50),
                         "null_pct": float(nullp) if nullp is not None else None})
        return {"rows": total, "files": len(self.paths), "columns": cols}

    def rows(self, offset=0, limit=50, sort=None, direction="asc", fcol=None, fq=None) -> dict:
        limit = max(1, min(int(limit), MAX_ROWS))
        offset = max(0, int(offset))
        where, args = "", []
        if fcol and fq:
            if fcol not in self.types:
                raise ValueError("unknown column")
            where = f" WHERE CAST({self.q(fcol)} AS VARCHAR) ILIKE ?"
            args.append(f"%{fq}%")
        order = ""
        if sort:
            if sort not in self.types:
                raise ValueError("unknown column")
            order = f" ORDER BY {self.q(sort)} {'DESC' if direction == 'desc' else 'ASC'} NULLS LAST"
        total = self.con.execute(f"SELECT count(*) FROM {self.rel}{where}", args).fetchone()[0]
        cur = self.con.execute(f"SELECT * FROM {self.rel}{where}{order} LIMIT {limit} OFFSET {offset}", args)
        names = [d[0] for d in cur.description]
        return {"total": total, "offset": offset, "limit": limit, "columns": names,
                "rows": [[jsonable(v) for v in r] for r in cur.fetchall()]}

    def profile(self, col: str) -> dict:
        """The chart for one column: a histogram for numbers, months for dates, top values otherwise."""
        if col not in self.types:
            raise ValueError("unknown column")
        c, typ = self.q(col), self.types[col].upper()
        nulls = self.con.execute(f"SELECT count(*) FILTER (WHERE {c} IS NULL), count(*) FROM {self.rel}").fetchone()
        base = {"column": col, "type": self.types[col], "nulls": nulls[0], "rows": nulls[1]}
        numeric = re.match(r"^(TINYINT|SMALLINT|INTEGER|BIGINT|HUGEINT|UTINYINT|USMALLINT|UINTEGER|UBIGINT|FLOAT|DOUBLE|DECIMAL)", typ)
        if typ.startswith(("DATE", "TIMESTAMP")):
            rows = self.con.execute(f"SELECT strftime(date_trunc('month', {c}), '%Y-%m') m, count(*) FROM {self.rel} "
                                    f"WHERE {c} IS NOT NULL GROUP BY 1 ORDER BY 1 LIMIT 240").fetchall()
            return dict(base, kind="months", items=[{"l": r[0], "v": r[1]} for r in rows])
        if numeric and typ != "BOOLEAN":
            lo, hi, distinct = self.con.execute(f"SELECT min({c}), max({c}), approx_count_distinct({c}) FROM {self.rel}").fetchone()
            if lo is None:
                return dict(base, kind="empty", items=[])
            if distinct and distinct > 20 and hi > lo:
                width = (float(hi) - float(lo)) / 20
                rows = self.con.execute(
                    f"SELECT least(19, CAST(floor((CAST({c} AS DOUBLE) - ?) / ?) AS INTEGER)) b, count(*) FROM {self.rel} "
                    f"WHERE {c} IS NOT NULL GROUP BY 1 ORDER BY 1", [float(lo), width]).fetchall()
                got = {r[0]: r[1] for r in rows}
                fmt = (lambda x: f"{x:.3g}")
                return dict(base, kind="histogram", items=[{"l": fmt(float(lo) + i * width), "v": got.get(i, 0)} for i in range(20)])
        rows = self.con.execute(f"SELECT CAST({c} AS VARCHAR), count(*) n FROM {self.rel} GROUP BY 1 ORDER BY n DESC, 1 LIMIT 15").fetchall()
        return dict(base, kind="top", items=[{"l": "∅ null" if r[0] is None else str(r[0]), "v": r[1]} for r in rows])

    def charts(self) -> dict:
        """The default charts for a mined table, from the columns it has."""
        out, names = {}, set(self.types)
        total = self.count()
        summ = self.con.execute(f"SUMMARIZE SELECT * FROM {self.rel}").fetchall()
        nulls = sorted(((r[0], float(r[11] or 0)) for r in summ if r[0] != "filename"), key=lambda x: -x[1])
        out["nulls"] = [{"l": n, "v": v} for n, v in nulls[:12] if v > 0]
        if "repo" in names:
            out["repos"] = [{"l": str(r[0]), "v": r[1]} for r in self.con.execute(
                f"SELECT repo, count(*) n FROM {self.rel} GROUP BY 1 ORDER BY n DESC LIMIT 15").fetchall()]
        for tc in TIME_COLUMNS:
            if tc in names and self.types[tc].upper().startswith(("DATE", "TIMESTAMP")):
                p = self.profile(tc)
                out["months"] = {"column": tc, "items": p["items"]}
                break
        labels = []
        for n, t in self.columns:
            if n.startswith("label_") and t.upper() == "BOOLEAN":
                r = self.con.execute(f"SELECT count(*) FILTER (WHERE {self.q(n)}), count(*) FILTER (WHERE NOT {self.q(n)}), "
                                     f"count(*) FILTER (WHERE {self.q(n)} IS NULL) FROM {self.rel}").fetchone()
                labels.append({"l": n, "vals": list(r)})
        out["labels"] = labels
        out["files"] = [{"l": os.path.basename(r[0]), "v": r[1]} for r in self.con.execute(
            f"SELECT filename, count(*) n FROM {self.rel} GROUP BY 1 ORDER BY 1").fetchall()]
        out["rows"] = total
        return out

    def close(self) -> None:
        self.con.close()


# -- text, JSON lines, JSON ----------------------------------------------------

def _check_size(path: str) -> int:
    size = os.path.getsize(path)
    if size > MAX_SCAN_BYTES:
        raise ValueError(f"file is {size:,} bytes; the viewer reads up to {MAX_SCAN_BYTES:,}")
    return size


def lines(path: str, offset=0, limit=200, q: str | None = None, tail: bool = False) -> dict:
    """A page of lines (or the last `limit` lines), optionally only those containing q."""
    _check_size(path)
    limit = max(1, min(int(limit), 2000))
    needle = (q or "").lower()
    out, total = [], 0
    if tail:
        keep = collections.deque(maxlen=limit)
        with open(path, "rb") as fh:
            for n, raw in enumerate(fh, 1):
                total = n
                if not needle or needle in raw.decode("utf-8", "replace").lower():
                    keep.append((n, raw.decode("utf-8", "replace").rstrip("\n")[:2000]))
        out = list(keep)
    else:
        with open(path, "rb") as fh:
            for n, raw in enumerate(fh, 1):
                s = raw.decode("utf-8", "replace")
                if needle and needle not in s.lower():
                    continue
                if total >= offset and len(out) < limit:
                    out.append((n, s.rstrip("\n")[:2000]))
                total += 1
    return {"total": total, "offset": offset, "limit": limit, "lines": out}


def jsonl_rows(path: str, offset=0, limit=50, q: str | None = None) -> dict:
    _check_size(path)
    limit = max(1, min(int(limit), MAX_ROWS))
    needle = (q or "").lower()
    cols: dict[str, None] = {}
    rows, total = [], 0
    with open(path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            if not raw.strip() or (needle and needle not in raw.lower()):
                continue
            if total >= offset and len(rows) < limit:
                try:
                    rec = json.loads(raw)
                except ValueError:
                    rec = {"_raw": raw[:300]}
                rec = rec if isinstance(rec, dict) else {"value": rec}
                for k in rec:
                    cols.setdefault(k)
                rows.append(rec)
            total += 1
    names = list(cols)
    return {"total": total, "offset": offset, "limit": limit, "columns": names,
            "rows": [[jsonable(r.get(c)) for c in names] for r in rows]}


def json_doc(path: str):
    if os.path.getsize(path) > MAX_JSON_BYTES:
        raise ValueError("JSON file is too large for the tree view; open it as text")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def trace_summary(path: str) -> dict:
    """Where a run's time went: seconds per stage, per-PR durations, levels, and the warnings."""
    _check_size(path)
    stages: dict[str, dict] = {}
    run_stages: dict[str, float] = collections.defaultdict(float)
    levels: collections.Counter = collections.Counter()
    prs, problems = [], []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            try:
                e = json.loads(raw)
            except ValueError:
                continue
            levels[e.get("lvl", "?")] += 1
            if e.get("lvl") in ("WARN", "ERROR") and len(problems) < 100:
                problems.append({"t": e.get("t"), "lvl": e.get("lvl"), "stage": e.get("stage"), "msg": e.get("msg"),
                                 "repo": e.get("repo"), "pr": e.get("pr")})
            if e.get("stage") == "pr" and e.get("msg") == "done":
                prs.append({"repo": e.get("repo"), "pr": e.get("pr"), "dur_s": e.get("dur_s") or 0})
                for name, v in (e.get("stages") or {}).items():
                    s = stages.setdefault(name, {"n": 0, "s": 0.0})
                    s["n"] += v.get("n", 0)
                    s["s"] += v.get("s", 0.0)
            elif e.get("stage") == "stage" and e.get("msg") == "done" and "dur_s" in e:
                run_stages[e.get("name") or "?"] += e["dur_s"]
    slow = sorted(prs, key=lambda p: -p["dur_s"])[:10]
    return {"events": sum(levels.values()), "levels": dict(levels), "prs": len(prs),
            "pr_stage_seconds": sorted(({"l": k, "v": round(v["s"], 1), "n": v["n"]} for k, v in stages.items()),
                                       key=lambda x: -x["v"])[:14],
            "run_stage_seconds": sorted(({"l": k, "v": round(v, 1)} for k, v in run_stages.items()), key=lambda x: -x["v"])[:12],
            "pr_durations": [round(p["dur_s"], 1) for p in prs][:500], "slowest": slow, "problems": problems}
