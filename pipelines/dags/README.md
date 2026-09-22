# dags

Airflow DAGs. Scheduling only — the work itself lives in `ingestion/` and
`training/` so it stays runnable without Airflow.

Expected DAGs: nightly ingestion, per-tenant retraining on a drift or age
trigger, and drift measurement feeding `riffle_drift_score`.
