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
  lead reads. When the lead plans a split (`--shard-plan N --issue-tokens`), the
  store mints one write token per PC, bound to that split's ID and optionally
  expiring, using a separate admin token that can issue and revoke but not read.
  Each PC gets a join code (store address, token, PC number, the plan's flags)
  and runs `join.py CODE`, which checks it with the store and writes `.env`. A
  token cannot write under another split, and a run turns sync off, saying why,
  if its token is rejected or its flags compute a different split.
- The store runs on one GCP VM from a Compose file, following ADR 0013
  (`infra/docker/riffle-data/`): one service for ingest and the pages, with a
  SQLite index on the VM's disk. Logs, health files and `run.json` stay on the
  disk so search is fast; every file is also in a GCS bucket, and datasets live
  only there. The VM uses its attached service account, not key
  files. The service is never published as plain HTTP. A public address goes
  through Caddy with an automatic certificate (the `public` Compose profile);
  a private one through an IAP tunnel or Tailscale. Read access is a token in
  an HttpOnly cookie, with a strict Content-Security-Policy and a limit on failed
  sign-ins per address.
- The pages on the VM follow the site's design and the miner web view. They show,
  per PC, repos done and total, the repo being mined, PRs mined and PRs left, health
  flags and file lists, with charts, log and trace search, and a file viewer that
  opens parquet as charts, a sortable table and a column list. PRs left
  covers repos already started: the miner selects a repo's PRs when it begins
  it, so a repo that has not started has no PR count.
- The tables are queried with DuckDB (`server/duck.py`), as ADR 0014 chooses for
  ad-hoc queries. It makes `mined_dataset`, `mined_snapshot` and
  `mined_commit_features` views over the PCs' parquet files, by default only for
  PCs that finished. The tables stay three (ADR 0012 and the miner's design).
- BigQuery is optional (`server/bq_load.py`, same three tables), for the case
  where several people need SQL with per-person access or dashboards. The
  BigQuery API is not enabled until someone needs it.

## Options not taken

| Option | Why not |
| --- | --- |
| S3 or MinIO API directly from each PC | Needs an S3 client library and signing on every PC, and a PC key can usually also read; a PUT endpoint needs neither |
| DVC or a shared bucket by hand | Manual, and gives no view of logs or run state |
| Continuous log streaming to Loki, as ADR 0014 uses for services | Real-time is not needed, and a stopped PC would still lose its last minutes; sync on events covers it |
| Syncing `ci_cache/` | Only the same PC resumes from it, and it can be large |
| BigQuery as the main query engine | The data fits one machine, ADR 0014 already picks DuckDB, and BigQuery adds a second copy, billing and IAM; kept as an optional loader |
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
- The join code is a secret the lead must send privately; anyone holding it can upload as that PC until it is rotated or revoked. The admin token is the most sensitive credential and lives in the lead's `.env`.
- A public address puts a login page on the internet; a leaked read token exposes all mined data until it is revoked.
- A shared VM is a new thing to run, patch and back up, and a GCP project with billing is needed before anything runs.
- The DuckDB views include only finished PCs unless asked (`--include-unfinished`); the BigQuery loader includes all unless given `--only-finished`.

- ADR 0014 names DVC for training data and Grafana, not a custom page, for dashboards. The pages here are miner-only and the bucket holds the same parquet files DVC would version. If ADR 0014 is accepted as written, the pages may move to Grafana.

## Revisit if

- The corpus or one PC's output grows past what a single PUT per file handles
  (the client already skips files over 2 GiB).
- More than one person needs to read, so per-person read access is required.
- A run needs live progress rather than state as of the last sync.

## Open

- Whether the optional BigQuery loader is kept once the team knows who needs SQL access.
- Who pays after the credit runs out. The project `riffle-data-miner` exists, with billing from the Education account and a budget alert; the VM and bucket are not created yet, and only the laptop setup is tested. A Gmail login has no GCP organisation, so the project sits outside one.
- How long the store keeps logs and traces.
- Whether the lead's merge (`merge_shards.py`) should read from the store
  instead of from files sent by hand.
