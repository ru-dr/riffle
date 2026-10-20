# dags

Airflow DAGs. Scheduling only — the work itself lives in `ingestion/` and
`training/` so it stays runnable without Airflow.

`riffle_smoke.py` is a check that the Airflow stack works; delete it once a real DAG
exists. The stack that runs these DAGs is in
[`infra/docker/airflow`](../../infra/docker/airflow).

Expected DAGs: nightly ingestion, per-tenant retraining on a drift or age
trigger, and drift measurement feeding `riffle_drift_score`.
