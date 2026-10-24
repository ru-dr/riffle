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
import sys

from tables import TABLES, finished, plan, table_for  # noqa: F401  (re-exported)

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", required=True, help="BigQuery dataset, created if missing")
    ap.add_argument("--version", required=True, help="miner version, the first path part")
    ap.add_argument("--split", required=True, help="split ID (see --shard-plan)")
    ap.add_argument("--bucket", default=os.environ.get("RIFFLE_BUCKET"))
    ap.add_argument("--project", default=os.environ.get("GOOGLE_CLOUD_PROJECT"))
    ap.add_argument("--location", default=os.environ.get("BQ_LOCATION", "US"))
    ap.add_argument("--n", type=int, default=None,
                    help="the split's current number of PCs: files of an earlier plan with another N are left out")
    ap.add_argument("--only-finished", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="print what would be loaded and stop")
    args = ap.parse_args(argv)
    if not args.bucket:
        sys.exit("set RIFFLE_BUCKET or pass --bucket")

    from google.cloud import bigquery, storage
    gcs = storage.Client(project=args.project)
    blobs = list(gcs.list_blobs(args.bucket))
    finished_pcs = None
    if args.only_finished:
        finished_pcs = set()
        for b in blobs:
            parts = b.name.split("/")
            if len(parts) == 4 and parts[1:] == [args.version, args.split, "run.json"]:
                try:
                    done = finished(json.loads(b.download_as_bytes()))
                except ValueError:
                    done = False
                if done:
                    finished_pcs.add(parts[0])
    files, notes = plan([b.name for b in blobs], args.version, args.split, finished_pcs, args.n)
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
    # all or nothing: every table is loaded into a staging table first, and only when all of them worked are the real
    # tables replaced (a copy job each), so a failure never leaves tables from two different loads side by side
    staged = []
    try:
        for table, keys in files.items():
            if not keys:
                continue
            cfg = bigquery.LoadJobConfig(source_format=bigquery.SourceFormat.PARQUET,
                                         write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE)
            tmp = f"{bq.project}.{args.dataset}.{table}__loading"
            job = bq.load_table_from_uri([f"gs://{args.bucket}/{k}" for k in keys], tmp, job_config=cfg)
            job.result()
            staged.append(table)
        for table in staged:
            cfg = bigquery.CopyJobConfig(write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE)
            bq.copy_table(f"{bq.project}.{args.dataset}.{table}__loading", f"{bq.project}.{args.dataset}.{table}",
                          job_config=cfg).result()
            print(f"[bq] {table}: {bq.get_table(f'{bq.project}.{args.dataset}.{table}').num_rows:,} rows")
    finally:
        for table in staged:
            bq.delete_table(f"{bq.project}.{args.dataset}.{table}__loading", not_found_ok=True)
    for table, keys in files.items():
        if not keys:                                        # nothing for it now: an older load of it would be stale
            bq.delete_table(f"{bq.project}.{args.dataset}.{table}", not_found_ok=True)
            print(f"[bq] {table}: no files now, the table was removed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
