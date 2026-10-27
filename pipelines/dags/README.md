# dags

Airflow DAGs. Scheduling only — the work itself lives in `ingestion/` and
`training/` so it stays runnable without Airflow.

`riffle_smoke.py` is a check that the Airflow stack works; delete it once a real DAG
exists. The stack that runs these DAGs is in
[`infra/docker/airflow`](../../infra/docker/airflow).

`riffle_miner.py` draws the repo miner as a graph: one task per per-repo stage
(`fetching PRs`, `timelines`, `szz`, `mining PRs`, ...), then gate, gather, merge, check and
publish. Mining runs on the team's PCs, not here: the stage tasks read the run traces in the store
and show each stage's status, time and errors, and the last tasks merge the PCs' tables, check them
against `contracts/` and version them with DVC. Trigger it with `miner_version`, `split_id` and
(to publish) `dvc_remote`. The stage list is `pipelines/ingestion/repo_miner/stages.py`; the
reasoning is in [ADR 0017](../../docs/adr/0017-miner-dag-loki-and-dvc.md). Publish prints a
`mined.dvc` pointer; commit it as `data/mined.dvc`.

Expected DAGs: nightly ingestion, per-tenant retraining on a drift or age
trigger, and drift measurement feeding `riffle_drift_score`.
