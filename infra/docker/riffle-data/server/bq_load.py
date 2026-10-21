"""
bq_load.py — load the PCs' mined tables from the bucket into BigQuery.

  python bq_load.py --dataset riffle_mined --version 6.6.0 --split abc123 [--only-finished] [--dry-run]

Reads gs://<RIFFLE_BUCKET>/<pc>/<version>/<split>/data/ for every PC and loads
the parquet tables into three BigQuery tables named after their contracts:

  mined_dataset           dataset_pcKofN.parquet
  mined_snapshot          dataset_pcKofN_snapshots.parquet
  mined_commit_features   dataset_pcKofN_commit_features.parquet

Each load replaces its table (WRITE_TRUNCATE) with the files found now, so it is
safe to run again as PCs finish. The three tables stay three: they have one row
per PR, per snapshot and per head commit, and are joined in SQL or by
pipelines/training/frames.py, never merged. The schema comes from the parquet
files themselves. `--only-finished` skips a PC whose run.json is not "batch
done", so a half-mined PC does not appear. A PC that wrote only JSONL (no
pandas/pyarrow) is reported and skipped. Uses the VM's service account; no key.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", required=True, help="BigQuery dataset, created if missing")
    ap.add_argument("--version", required=True, help="miner version, the first path part")
    ap.add_argument("--split", required=True, help="split ID (see --shard-plan)")
    ap.add_argument("--bucket", default=os.environ.get("RIFFLE_BUCKET"))
    ap.add_argument("--project", default=os.environ.get("GOOGLE_CLOUD_PROJECT"))
    ap.add_argument("--location", default=os.environ.get("BQ_LOCATION", "US"))
    ap.add_argument("--only-finished", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="print what would be loaded and stop")
    args = ap.parse_args(argv)
    if not args.bucket:
        sys.exit("set RIFFLE_BUCKET or pass --bucket")

    from google.cloud import bigquery, storage
    gcs = storage.Client(project=args.project)
    blobs = list(gcs.list_blobs(args.bucket))
    finished = None
    if args.only_finished:
        finished = set()
        for b in blobs:
            parts = b.name.split("/")
            if len(parts) == 4 and parts[1:] == [args.version, args.split, "run.json"]:
                if json.loads(b.download_as_bytes()).get("reason") == "batch done":
                    finished.add(parts[0])
    files, notes = plan([b.name for b in blobs], args.version, args.split, finished)
    for n in notes:
        print(f"[bq] {n}")
    if not any(files.values()):
        print("[bq] nothing to load")
        return 1
    for table, keys in files.items():
        print(f"[bq] {table}: {len(keys)} files" + "".join(f"\n       gs://{args.bucket}/{k}" for k in keys))
    if args.dry_run:
        return 0

    bq = bigquery.Client(project=args.project, location=args.location)
    ds = bigquery.Dataset(f"{bq.project}.{args.dataset}")
    ds.location = args.location
    bq.create_dataset(ds, exists_ok=True)
    for table, keys in files.items():
        if not keys:
            continue
        cfg = bigquery.LoadJobConfig(source_format=bigquery.SourceFormat.PARQUET,
                                     write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE)
        job = bq.load_table_from_uri([f"gs://{args.bucket}/{k}" for k in keys],
                                     f"{bq.project}.{args.dataset}.{table}", job_config=cfg)
        job.result()
        print(f"[bq] {table}: {bq.get_table(job.destination).num_rows:,} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
