"""Shared test helpers: a tiny stand-in for the dataset, and a fake downloader.

A small Parquet file stands in for each as_is table, and the inventory is built from those
files, so the checksums and row counts the code verifies are real.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from aidev_ingest.closure import CHILDREN_WITH_PR_ID
from aidev_ingest.hub import summarize_parquet
from aidev_ingest.pin import as_is_tables
from aidev_ingest.verify import sha256_file

REV = "c" * 40


def write_parquet(path: Path, rows: int, extra: str) -> None:
    table = pa.table(
        {"id": pa.array(range(1, rows + 1), pa.int64()), extra: [f"{extra}{i}" for i in range(rows)]}
    )
    pq.write_table(table, path)


def build_world(tmp_path: Path):
    """Source files for every as_is table (distinct sizes), an inventory describing them, and
    a folder to download into."""
    source = tmp_path / "source"
    source.mkdir()
    files = {}
    for index, name in enumerate(as_is_tables()):
        path = source / f"{name}.parquet"
        rows = 10 * (index + 1)
        if name in CHILDREN_WITH_PR_ID:  # the closure check reads their pr_id column
            table = pa.table(
                {"id": pa.array(range(1, rows + 1), pa.int64()), "pr_id": pa.array(range(1, rows + 1), pa.int64())}
            )
            pq.write_table(table, path)
        else:
            write_parquet(path, rows=rows, extra="v" * (index + 1))
        files[name] = path
    entries = []
    for name, path in files.items():
        with open(path, "rb") as handle:
            info = summarize_parquet(handle)
        entries.append(
            {
                "path": path.name,
                "size": path.stat().st_size,
                "lfs_sha256": sha256_file(path),
                "blob_id": "b",
                "table": name,
                "parquet": {
                    "rows": info.rows,
                    "row_groups": info.row_groups,
                    "columns": [{"name": n, "type": t} for n, t in info.columns],
                },
            }
        )
    inventory = {"schema_version": 1, "repo_id": "example/repo", "revision": REV, "files": entries}
    return inventory, files, tmp_path / "raw"


class FakeFetcher:
    def __init__(self, files: dict[str, Path], corrupt: set[str] | None = None, fail_on: str | None = None):
        self.files, self.corrupt, self.fail_on = files, corrupt or set(), fail_on
        self.calls: list[str] = []

    def download_file(self, path: str, revision: str, dest_dir: Path) -> Path:
        name = Path(path).stem
        self.calls.append(name)
        if name == self.fail_on:
            (dest_dir / path).write_bytes(b"half a file")
            raise ConnectionError("network dropped")
        target = dest_dir / path
        shutil.copyfile(self.files[name], target)
        if name in self.corrupt:
            data = bytearray(target.read_bytes())
            data[len(data) // 2] ^= 0xFF
            target.write_bytes(bytes(data))
        return target
