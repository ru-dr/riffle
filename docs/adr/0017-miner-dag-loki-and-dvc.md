# 0017: A miner DAG, Loki for run traces, and DVC for merged tables

- **Date:** 2026-10-27
- **Status:** Proposed
- **Owners:** #5 Data ingestion, with #10 Deployment & CI/CD

## Context

The miner runs on the team's PCs and syncs its run log, trace and tables to the store ([ADR 0015](0015-central-store-for-mined-data.md)). The lead can read one PC's trace in the store pages, but cannot see which miner stage (timelines, szz, mining PRs) each repo is in across all PCs, or chart stage times over several runs. Airflow runs on one VM ([ADR 0013](0013-airflow-on-a-compose-vm.md)) and must not run mining. The merged tables from `merge_shards.py` have no version history: ADR 0015 rejects DVC for the PCs' hand-offs, and the longer-term stack says DVC and a bucket for data.

## Decision

- One DAG, `riffle_miner` (`pipelines/dags/riffle_miner.py`), triggered by hand with a miner version and split ID. It has one task per per-repo stage, in the order `mine_repo.py` runs them. The list is `pipelines/ingestion/repo_miner/stages.py`, and a test fails if it differs from the `runlog.stage()` calls in `mine_repo.py`.
- Stage tasks only read the run traces under the store's disk and report status, time and errors. They do not mine. A stage with an ERROR event is red, one not yet reached or still running is skipped.
- When every stage is done: gather the PCs' files, run `merge_shards.py`, check the merged tables' columns against their schemas in `contracts/`, and version them with DVC (`publish.py`).
- DVC tracks only the merged tables and their merge manifest, under `mined/<miner version>/`, in a workspace made with `dvc init --no-scm` and pushed to a GCS remote with the VM's service account. The DAG prints the `mined.dvc` pointer; a person commits it as `data/mined.dvc`. Per-PC files keep going through `sync.py`.
- The miner's run trace (`runlog.py`) stays the only log. The store keeps each trace file and forwards every new line to Loki as it arrives (`server/loki.py`), so no agent tails the disk and nothing else writes to Loki; PCs never contact it. Labels are `pc`, `miner_version`, `split`, `run`, `lvl`, `component` (the trace's `stage` field) and `miner_stage`; repo and PR number are structured metadata; retention is 90 days.
- A watched run uploads only the new complete trace lines (`X-Append-Offset`, accepted by stores that say `append` in `/v1/whoami`), every 10 s, instead of the whole file. Any mismatch or an older store falls back to the whole-file PUT. The bucket copy is brought up to date every 5 minutes and when the run ends.
- The store's log search reads Loki when it is set up (the files are the fallback), and the admin page has a Run tracking panel: Loki's state, lines sent, queue, last error, and links to Grafana and Airflow. There is one Loki and one Grafana, the project's logging stack in `infra/compose.logging.yaml` (`infra/loki`, `infra/grafana`); the store joins its network through `infra/docker/riffle-data/compose.logs.yaml`, an override file, so the store starts without it. The miner dashboard is provisioned in `infra/grafana/provisioning/dashboards/`. Traces reach Loki only through the store, never also through Alloy's drop folder, so no line arrives twice.

## Options not taken

| Option | Why not |
| --- | --- |
| One Airflow task per stage that runs the stage | The stages share one process, an in-memory history cache and a checkpoint; splitting them means re-architecting the miner, and mining must stay off the Airflow VM |
| Grafana Alloy tails the store's disk | A second reader of the same files: every whole-file re-upload is read again and needs de-duplication, and the store's own search would remain a second log search |
| PCs push logs to Loki (Alloy or Promtail on each PC) | ADR 0015 rejected continuous streaming: volunteers' PCs would need an agent and a route to Loki, and a stopped PC still loses its last lines |
| DVC for every PC's output | Duplicates `sync.py`, needs a DVC remote credential on every PC, and ADR 0015 already decided against it |
| Pointer file committed by the DAG | The Airflow VM has no git credentials and should not get any |
| Keep search in the store pages only | No cross-run charts, and no alert path |

## Consequences

- New: `stages.py`, `publish.py` and their tests in `pipelines/ingestion/repo_miner`, the DAG, `server/loki.py` and `compose.logs.yaml` in `infra/docker/riffle-data`, the miner dashboard in `infra/grafana`, and an append upload in `sync.py`.
- The Airflow image gains pandas, pyarrow, DVC and `google-cloud-storage`, and its Compose file mounts `pipelines/ingestion`, `contracts` and the store's volume read-only. No contract changes, so no miner version bump.
- The store remembers how much of each trace Loki has (`loki_offsets` in its index), so a Loki that was down gets what it missed from the file; repeated lines are dropped by Loki.
- Grafana needs its own admin password in `.env`. No invariant is touched: the LLM is not on any scoring path and nothing leaves the installation.

## Revisit if

- Mining moves to Kubernetes Jobs: stages could then become separate tasks.
- The store VM's memory cannot hold Loki beside the store, Airflow and DuckDB.
- More than one team reads the dashboards and needs per-person access.

## Open

- Whether the Airflow image's constraints allow `dvc[gs]` and pandas at the pinned Airflow version; not yet built.
- Where the lead wants the DVC remote bucket and its retention.
