# 0014: A central store for mined data and run logs

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
- The store runs on one VM from a Compose file, following ADR 0013: an ingest
  service, an object store and a small index of runs and repos, reachable on a
  private network only. A viewer (log search, per-repo health, a page in
  `site/`) is built on that index in later PRs.

## Options not taken

| Option | Why not |
| --- | --- |
| S3 or MinIO API directly from each PC | Needs an S3 client library and signing on every PC, and a PC key can usually also read; a PUT endpoint needs neither |
| DVC or a shared bucket by hand | Manual, and gives no view of logs or run state |
| Continuous log streaming (Loki or similar) | Real-time is not needed, and a stopped PC would still lose its last minutes; sync on events covers it |
| Syncing `ci_cache/` | Only the same PC resumes from it, and it can be large |
| Managed cloud storage and dashboards | Ongoing cost, and the data would leave the team's own machine |

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
- A shared VM is a new thing to run, patch and back up.

## Revisit if

- The corpus or one PC's output grows past what a single PUT per file handles
  (the client already skips files over 2 GiB).
- More than one person needs to read, so per-person read access is required.
- A run needs live progress rather than state as of the last sync.

## Open

- Where the VM lives and who pays for it.
- How long the store keeps logs and traces.
- Whether the lead's merge (`merge_shards.py`) should read from the store
  instead of from files sent by hand.
