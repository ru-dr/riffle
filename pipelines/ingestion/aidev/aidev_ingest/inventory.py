"""Describe a pinned AIDev revision, and detect when it no longer matches.

The inventory is the expected manifest: for every file at the pinned commit, its
size, its Git LFS sha256 (the hub's own checksum, so nothing has to be downloaded
to know it) and, for Parquet, row count and column types. Later stages compare
what they download against it.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from .hub import Source
from .pin import TABLES, planned_tables

SCHEMA_VERSION = 1


def table_name(path: str) -> str:
    """`pr_timeline.parquet` is the table `pr_timeline`."""
    return PurePosixPath(path).stem


def build_inventory(source: Source, repo_id: str, revision: str) -> dict[str, Any]:
    files = []
    for info in sorted(source.list_files(revision), key=lambda f: f.path):
        entry: dict[str, Any] = {
            "path": info.path,
            "size": info.size,
            "lfs_sha256": info.lfs_sha256,
            "blob_id": info.blob_id,
        }
        if info.path.endswith(".parquet"):
            parquet = source.parquet_info(info.path, revision)
            entry["table"] = table_name(info.path)
            entry["parquet"] = {
                "rows": parquet.rows,
                "row_groups": parquet.row_groups,
                "columns": [{"name": n, "type": t} for n, t in parquet.columns],
            }
        files.append(entry)
    return {
        "schema_version": SCHEMA_VERSION,
        "repo_id": repo_id,
        "revision": revision,
        "files": files,
    }


def _columns(entry: dict[str, Any]) -> dict[str, str]:
    return {c["name"]: c["type"] for c in entry["parquet"]["columns"]}


def compare(expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    """Differences between two inventories, as readable lines. Empty means identical.

    `meta` (timestamps, library versions) is not part of the comparison.
    """
    problems: list[str] = []
    for key in ("schema_version", "repo_id", "revision"):
        if expected.get(key) != actual.get(key):
            problems.append(f"{key}: expected {expected.get(key)!r}, found {actual.get(key)!r}")

    want = {f["path"]: f for f in expected["files"]}
    have = {f["path"]: f for f in actual["files"]}
    for path in sorted(want.keys() - have.keys()):
        problems.append(f"{path}: missing")
    for path in sorted(have.keys() - want.keys()):
        problems.append(f"{path}: not in the expected inventory")

    for path in sorted(want.keys() & have.keys()):
        old, new = want[path], have[path]
        for key in ("size", "lfs_sha256", "blob_id"):
            if old.get(key) != new.get(key):
                problems.append(f"{path}: {key} expected {old.get(key)!r}, found {new.get(key)!r}")
        if "parquet" not in old and "parquet" not in new:
            continue
        if "parquet" not in old or "parquet" not in new:
            problems.append(f"{path}: parquet metadata present on one side only")
            continue
        for key in ("rows", "row_groups"):
            if old["parquet"][key] != new["parquet"][key]:
                problems.append(f"{path}: {key} expected {old['parquet'][key]}, found {new['parquet'][key]}")
        old_cols, new_cols = _columns(old), _columns(new)
        for name in sorted(old_cols.keys() - new_cols.keys()):
            problems.append(f"{path}: column {name!r} missing")
        for name in sorted(new_cols.keys() - old_cols.keys()):
            problems.append(f"{path}: unexpected column {name!r}")
        for name in sorted(old_cols.keys() & new_cols.keys()):
            if old_cols[name] != new_cols[name]:
                problems.append(f"{path}: column {name!r} is {new_cols[name]}, expected {old_cols[name]}")
    return problems


def check_plan(inventory: dict[str, Any]) -> list[str]:
    """Problems that would stop the planned ingestion, given what the revision holds."""
    tables = {f["table"]: f for f in inventory["files"] if "table" in f}
    problems = []
    for name in planned_tables():
        if name not in tables:
            problems.append(f"planned table {name!r} is not in this revision")
            continue
        columns = _columns(tables[name])
        for dropped in TABLES[name].drop_columns:
            if dropped not in columns:
                problems.append(f"{name}: column {dropped!r} to drop is not in the table")
    return problems


def summary(inventory: dict[str, Any]) -> str:
    """A fixed-width table for people. The JSON is for machines."""
    lines = [
        f"{inventory['repo_id']} @ {inventory['revision']}",
        f"{'file':<28}{'rows':>12}{'size MB':>10}{'cols':>6}  plan",
    ]
    for entry in inventory["files"]:
        table = entry.get("table")
        plan = TABLES[table].fetch if table in TABLES else ("-" if table else "")
        if "parquet" in entry:
            rows = f"{entry['parquet']['rows']:,}"
            cols = str(len(entry["parquet"]["columns"]))
        else:
            rows, cols = "", ""
        lines.append(f"{entry['path']:<28}{rows:>12}{entry['size'] / 1e6:>10.1f}{cols:>6}  {plan}")
    return "\n".join(lines)
