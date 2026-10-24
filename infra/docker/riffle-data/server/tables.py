"""
tables.py — which uploaded files belong to which mined table.

Shared by duck.py and bq_load.py. Keys look like `<pc>/<version>/<split>/data/<file>`.
"""
from __future__ import annotations

import re

TABLES = (("mined_dataset", re.compile(r"^dataset(?:.*pc\d+of\d+)?\.parquet$")),
          ("mined_snapshot", re.compile(r"^dataset.*_snapshots\.parquet$")),
          ("mined_commit_features", re.compile(r"^dataset.*_commit_features\.parquet$")))


def table_for(filename: str) -> str | None:
    """The BigQuery table a data file belongs to, or None (checkpoint, manifest, JSONL)."""
    for table, pat in TABLES[1:]:                       # the dataset pattern also matches the others' names
        if pat.match(filename):
            return table
    return TABLES[0][0] if TABLES[0][1].match(filename) else None


PC_OF = re.compile(r"^pc\d+of(\d+)(?:-|$)")


def version_key(v: str) -> tuple:
    """A sort key that puts 6.9.0 before 6.13.0: the numeric parts compare as numbers, anything else as text."""
    return tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in re.split(r"[.\-+]", v or ""))


def plan(keys: list[str], version: str, split: str, finished: set[str] | None = None,
         n: int | None = None) -> tuple[dict, list[str]]:
    """({table: [keys]}, [notes]) from the bucket's keys `<pc>/<version>/<split>/data/<file>`.
    `finished`, when given, is the set of PCs allowed. `n` is the split's current number of PCs: a split planned
    again with another N leaves the old `pc<k>of<M>-<split>` files in the store, and they hold the same PRs, so a
    PC of another generation is left out (its files stay where they are). PC names that are not `pc<k>of<N>` are kept."""
    out: dict[str, list[str]] = {t: [] for t, _ in TABLES}
    notes, jsonl_only, old_gen = [], set(), set()
    for key in sorted(keys):
        parts = key.split("/")
        if len(parts) != 5 or parts[1:4] != [version, split, "data"]:
            continue
        pc, name = parts[0], parts[4]
        m = PC_OF.match(pc)
        if n is not None and m and int(m.group(1)) != n:
            old_gen.add(pc)
            continue
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
    if old_gen:
        notes.append(f"left out, from an earlier plan of this split (not {n} PCs): {', '.join(sorted(old_gen))}")
    if n is None:
        sizes = {PC_OF.match(k.split("/")[0]).group(1) for k in keys
                 if len(k.split("/")) == 5 and k.split("/")[1:4] == [version, split, "data"] and PC_OF.match(k.split("/")[0])}
        if len(sizes) > 1:
            notes.append(f"PCs of several plans of this split are mixed ({', '.join(sorted(sizes, key=int))} PCs): "
                         f"rows may repeat; pass the current number of PCs")
    if finished is not None:
        notes.append(f"only finished PCs: {', '.join(sorted(finished)) or 'none'}")
    return out, notes


def finished(status) -> bool:
    """Does this run.json say the PC finished and sent everything? "batch done" with files that failed or were left
    out (a client older than the "batch incomplete" reason can still send it) is not finished."""
    return (isinstance(status, dict) and status.get("reason") == "batch done"
            and not status.get("files_failed") and not status.get("files_deferred"))
