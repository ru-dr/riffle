"""
tables.py — which uploaded files belong to which mined table.

Shared by duck.py and bq_load.py. Keys look like `<pc>/<version>/<split>/data/<file>`.
"""
from __future__ import annotations

import re

TABLES = (("mined_dataset", re.compile(r"^dataset.*pc\d+of\d+\.parquet$")),
          ("mined_snapshot", re.compile(r"^dataset.*_snapshots\.parquet$")),
          ("mined_commit_features", re.compile(r"^dataset.*_commit_features\.parquet$")))


def table_for(filename: str) -> str | None:
    """The BigQuery table a data file belongs to, or None (checkpoint, manifest, JSONL)."""
    for table, pat in TABLES[1:]:                       # the dataset pattern also matches the others' names
        if pat.match(filename):
            return table
    return TABLES[0][0] if TABLES[0][1].match(filename) else None


def plan(keys: list[str], version: str, split: str, finished: set[str] | None = None) -> tuple[dict, list[str]]:
    """({table: [keys]}, [notes]) from the bucket's keys `<pc>/<version>/<split>/data/<file>`.
    `finished`, when given, is the set of PCs allowed."""
    out: dict[str, list[str]] = {t: [] for t, _ in TABLES}
    notes, jsonl_only = [], set()
    for key in sorted(keys):
        parts = key.split("/")
        if len(parts) != 5 or parts[1:4] != [version, split, "data"]:
            continue
        pc, name = parts[0], parts[4]
        if finished is not None and pc not in finished:
            continue
        table = table_for(name)
        if table:
            out[table].append(key)
        elif name.endswith(".jsonl") and not name.endswith(".partial.jsonl"):
            jsonl_only.add(pc)
    have = {k.split("/")[0] for ks in out.values() for k in ks}
    for pc in sorted(jsonl_only - have):
        notes.append(f"{pc}: JSONL only, no parquet; skipped (install pandas and pyarrow on that PC)")
    if finished is not None:
        notes.append(f"only finished PCs: {', '.join(sorted(finished)) or 'none'}")
    return out, notes
