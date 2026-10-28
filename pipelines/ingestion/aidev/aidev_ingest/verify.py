"""Check a downloaded file against what the inventory says it must be."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .hub import summarize_parquet

CHUNK = 1024 * 1024
SAFE_NAME = re.compile(r"[A-Za-z0-9_]+")


@dataclass(frozen=True)
class Expected:
    """What the committed inventory says one table's file must look like."""

    table: str
    path: str
    size: int
    sha256: str | None
    rows: int
    columns: tuple[tuple[str, str], ...]  # (name, arrow type), in order


def expected_tables(inventory: dict[str, Any]) -> dict[str, Expected]:
    found: dict[str, Expected] = {}
    for entry in inventory["files"]:
        if "table" not in entry:
            continue
        if not SAFE_NAME.fullmatch(entry["table"]):
            # Table names become file names; the inventory is a committed file, not a trusted one.
            raise ValueError(f"unsafe table name in the inventory: {entry['table']!r}")
        parquet = entry["parquet"]
        found[entry["table"]] = Expected(
            table=entry["table"],
            path=entry["path"],
            size=entry["size"],
            sha256=entry["lfs_sha256"],
            rows=parquet["rows"],
            columns=tuple((c["name"], c["type"]) for c in parquet["columns"]),
        )
    return found


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def verify_file(path: Path, expected: Expected) -> list[str]:
    """Why this file is not the expected one. An empty list means it is.

    Size is checked first and the checksum only if the size matches: a file of the
    wrong size cannot have the right checksum, and hashing a gigabyte to say so is
    wasted work. The Parquet footer (rows, columns) is read only when size and
    checksum already agree, so bytes that failed them are never parsed.
    """
    if not path.is_file():
        return ["file is missing"]
    problems: list[str] = []

    size = path.stat().st_size
    if size != expected.size:
        problems.append(f"size is {size}, expected {expected.size}")
    elif expected.sha256 is None:
        problems.append("the inventory has no checksum for this file")
    else:
        digest = sha256_file(path)
        if digest != expected.sha256:
            problems.append(f"sha256 is {digest[:12]}..., expected {expected.sha256[:12]}...")

    if problems:
        # The bytes are already known to be wrong. Do not also parse them as Parquet: that is
        # parsing untrusted input to say something we have already established.
        return problems

    try:
        with open(path, "rb") as handle:
            info = summarize_parquet(handle)
    except Exception as error:  # pyarrow raises several types for a damaged footer
        problems.append(f"not readable as Parquet ({type(error).__name__})")
    else:
        if info.rows != expected.rows:
            problems.append(f"{info.rows} rows, expected {expected.rows}")
        if info.columns != expected.columns:
            problems.append("column names or types differ from the inventory")
    return problems
