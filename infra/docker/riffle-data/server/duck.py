"""
duck.py — query the mined tables with DuckDB, straight from the store (ADR 0015).

  python duck.py --version 6.6.0 --split abc123 -c "select repo, count(*) from mined_dataset group by 1"
  python duck.py --version 6.6.0 --split abc123 --db mined.duckdb      # then: duckdb mined.duckdb

Makes three views named after the contracts, over every PC's parquet files:

  mined_dataset           one row per closed PR
  mined_snapshot          one row per PR moment while open
  mined_commit_features   one row per head commit

The three stay three: they have different grains. Join them in SQL, or load them
with pipelines/training/frames.py. Each view also has a `filename` column saying
which PC's file a row came from.

Where the files come from: --bucket (or RIFFLE_BUCKET) downloads them to a local
cache with your Google login (`gcloud auth application-default login`) or the
VM's service account, skipping files that have not changed; --local DIR reads the
store's disk (the Compose volume) directly. By default only PCs whose run.json
says "batch done" are included, so a half-mined PC does not skew a result;
--include-unfinished adds them. Needs `pip install duckdb`.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from tables import TABLES, plan


class LocalSource:
    """The store's own disk: <root>/<pc>/<version>/<split>/..."""

    def __init__(self, root: str):
        self.root = root

    def keys(self) -> list[str]:
        out = []
        for dirpath, _d, names in os.walk(self.root):
            out += [os.path.relpath(os.path.join(dirpath, n), self.root).replace(os.sep, "/") for n in names]
        return out

    def path(self, key: str) -> str:
        return os.path.join(self.root, *key.split("/"))

    def text(self, key: str) -> str:
        with open(self.path(key), encoding="utf-8") as fh:
            return fh.read()


class GcsSource:
    """The bucket, mirrored into a local cache so DuckDB reads plain files."""

    def __init__(self, bucket: str, cache: str, project: str | None = None):
        from google.cloud import storage
        self.client = storage.Client(project=project)
        self.bucket, self.cache = bucket, cache
        self._blobs: dict[str, object] = {}

    def keys(self) -> list[str]:
        self._blobs = {b.name: b for b in self.client.list_blobs(self.bucket)}
        return list(self._blobs)

    def path(self, key: str) -> str:
        blob = self._blobs[key]
        dest = os.path.join(self.cache, *key.split("/"))
        stamp = dest + ".gen"
        if not (os.path.isfile(dest) and os.path.isfile(stamp) and open(stamp).read() == str(blob.generation)):
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            print(f"[duck] downloading {key} ({blob.size:,} bytes)", file=sys.stderr)
            blob.download_to_filename(dest)
            with open(stamp, "w") as fh:
                fh.write(str(blob.generation))
        return dest

    def text(self, key: str) -> str:
        return self._blobs[key].download_as_text()


def finished_pcs(source, keys: list[str], version: str, split: str) -> set[str]:
    out = set()
    for key in keys:
        parts = key.split("/")
        if len(parts) == 4 and parts[1:] == [version, split, "run.json"]:
            try:
                if json.loads(source.text(key)).get("reason") == "batch done":
                    out.add(parts[0])
            except (OSError, ValueError):
                continue
    return out


def connect(source, version: str, split: str, include_unfinished: bool = False, db: str = ":memory:"):
    """A DuckDB connection with the three views. Returns (connection, notes)."""
    import duckdb
    keys = source.keys()
    allowed = None if include_unfinished else finished_pcs(source, keys, version, split)
    files, notes = plan(keys, version, split, allowed)
    con = duckdb.connect(db)
    for table, _pat in TABLES:
        if not files[table]:
            continue
        paths = [source.path(k).replace("'", "''") for k in files[table]]
        listing = ", ".join(f"'{p}'" for p in paths)
        con.execute(f"CREATE OR REPLACE VIEW {table} AS "
                    f"SELECT * FROM read_parquet([{listing}], union_by_name=true, filename=true)")
    return con, notes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", required=True)
    ap.add_argument("--split", required=True)
    ap.add_argument("--bucket", default=os.environ.get("RIFFLE_BUCKET"))
    ap.add_argument("--project", default=os.environ.get("GOOGLE_CLOUD_PROJECT"))
    ap.add_argument("--local", help="read the store's disk (a directory of <pc>/<version>/<split>/...) instead of a bucket")
    ap.add_argument("--cache", default=os.path.expanduser("~/.cache/riffle-data"))
    ap.add_argument("--include-unfinished", action="store_true")
    ap.add_argument("--db", default=":memory:", help="write a DuckDB file holding the views, to open later")
    ap.add_argument("-c", "--command", help="SQL to run; prints the result")
    args = ap.parse_args(argv)
    if args.local:
        source = LocalSource(args.local)
    elif args.bucket:
        source = GcsSource(args.bucket, args.cache, args.project)
    else:
        sys.exit("pass --bucket (or set RIFFLE_BUCKET) or --local DIR")
    con, notes = connect(source, args.version, args.split, args.include_unfinished, args.db)
    for n in notes:
        print(f"[duck] {n}", file=sys.stderr)
    views = [r[0] for r in con.execute("SELECT view_name FROM duckdb_views() WHERE NOT internal").fetchall()]
    if not views:
        print("[duck] no tables found (are the PCs finished? try --include-unfinished)", file=sys.stderr)
        return 1
    print(f"[duck] views: {', '.join(views)}", file=sys.stderr)
    if args.command:
        cur = con.execute(args.command)
        rows = [[("" if v is None else str(v)) for v in r] for r in cur.fetchall()]
        head = [d[0] for d in cur.description]
        width = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h) for i, h in enumerate(head)]
        for line in [head] + rows:
            print("  ".join(c.ljust(w) for c, w in zip(line, width)).rstrip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
