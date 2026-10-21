# 0015: A central store for mined data and run logs

- **Date:** 2026-10-21
- **Status:** Proposed
- **Owners:** #5 Data ingestion, with #10 Deployment & CI/CD

## Context

The repo miner runs on several PCs, each with its own share of the repos
(`--shard K/N`). Today each person sends their dataset, snapshots and shard
manifest to the lead by hand, and nobody but the person running it can see a
run's log, trace or health files (`pipelines/ingestion/repo_miner/RUN_GUIDE.md`
step 7 says "the team bucket or DVC, as the lead says", which is not decided).
The lead wants to see every PC's logs and mined data in one place, and wants a
paused run to bring that place up to date. Real-time streaming is not needed.
`ci_cache/` is what a resume reads, and a resume happens on the same PC.
[ADR 0014](0014-mlops-tooling-stack.md) (Proposed) fixes the longer-term stack: DVC and a bucket for data, DuckDB for ad-hoc queries, and Loki and Grafana for the services' logs. This ADR covers the mining phase only, where volunteers' PCs send output to one place.

## Decision

- The miner syncs on events, not continuously: after each finished repo, on a
  Stop (ctrl+c or the TUI's Stop) and at the end of a batch.
- It sends the run log and trace, `health/*.json`, the dataset, its snapshot and
  commit-feature tables, the `.partial.jsonl` checkpoint and the shard manifest.
  `ci_cache/`, `snapshots_raw/` and clones stay on the PC.
- Transport is `PUT <RIFFLE_SYNC_URL>/v1/files/<key>` with a per-PC bearer token
  (`RIFFLE_SYNC_TOKEN`) and an `X-Content-SHA256` header, from the standard
  library (`pipelines/ingestion/repo_miner/sync.py`). The keys are
  `<miner_version>/<split_id>/{data,logs,health}/<file>` plus a `run.json`; the
  store adds the PC's name from the token.
- Sync is off unless both variables are set. It never fails a run: errors are
  warnings, an unreachable store ends that sync early, and a Stop waits at most
  `RIFFLE_SYNC_TIMEOUT` seconds (default 120). Unchanged files are skipped.
- Tokens are write-only and one per PC, so one can be revoked alone. Only the
  lead reads.
- The store runs on one GCP VM from a Compose file, following ADR 0013
  (`infra/docker/riffle-data/`): one service for ingest and the pages, with a
  SQLite index on the VM's disk. Logs, health files and `run.json` stay on the
  disk so search is fast; every file is also in a GCS bucket, and datasets and
  traces live only there. The VM uses its attached service account, not key
  files. The service is reachable only through a tunnel (IAP or Tailscale) or a
  TLS proxy, never as plain HTTP on the internet.
- The pages on the VM show, per PC, repos done and total, the repo being mined,
  PRs mined and PRs left, health flags and file lists, plus log search. PRs left
  covers repos already started: the miner selects a repo's PRs when it begins
  it, so a repo that has not started has no PR count.
- BigQuery gets the parquet tables from the bucket with `bq_load.py`, as
  `mined_dataset`, `mined_snapshot` and `mined_commit_features`, replacing each
  table on every load. The tables stay three (ADR 0012 and the miner's design).

## Options not taken

| Option | Why not |
| --- | --- |
| S3 or MinIO API directly from each PC | Needs an S3 client library and signing on every PC, and a PC key can usually also read; a PUT endpoint needs neither |
| DVC or a shared bucket by hand | Manual, and gives no view of logs or run state |
| Continuous log streaming to Loki, as ADR 0014 uses for services | Real-time is not needed, and a stopped PC would still lose its last minutes; sync on events covers it |
| Syncing `ci_cache/` | Only the same PC resumes from it, and it can be large |
| A hosted dashboard (Grafana Cloud and similar) | Ongoing cost and another account; the pages here are small and read the same index |

## Consequences

- `build_dataset.py` gains `--sync/--no-sync`; the miner moves to 6.6.0 with
  identical output.
- Mined data is public GitHub metadata, but the store stays private. Nothing
  leaves the installation in the product sense: this is the training pipeline,
  not the scoring path (`pipelines/README.md`).
- A PC's last sync is its state if it dies hard. Rows after the last repo
  boundary exist only in its local checkpoint.
- Sync needs the store reachable at stop time; a Stop with no network waits for
  the timeout, then leaves the files for the next sync.
- A shared VM is a new thing to run, patch and back up, and a GCP project with billing is needed before anything runs.
- The BigQuery tables can hold partly mined PCs unless `--only-finished` is used.

- ADR 0014 names DVC, not BigQuery, for training data, and Grafana, not a custom page, for dashboards. BigQuery here is an extra copy for analysis and the pages are miner-only. If ADR 0014 is accepted as written, one of them should change, or this ADR should say why the mining phase differs.

## Revisit if

- The corpus or one PC's output grows past what a single PUT per file handles
  (the client already skips files over 2 GiB).
- More than one person needs to read, so per-person read access is required.
- A run needs live progress rather than state as of the last sync.

## Open

- Whether BigQuery stays once ADR 0014 is decided, or DuckDB over the bucket's parquet replaces it.
- Which GCP organisation and project hold the VM, bucket and BigQuery dataset, and who pays. Nothing has been created or run on GCP yet; only the laptop setup is tested.
- How long the store keeps logs and traces.
- Whether the lead's merge (`merge_shards.py`) should read from the store
  instead of from files sent by hand.
