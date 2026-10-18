"""Smoke test for the Airflow stack. Proves the scheduler picks up DAGs from
pipelines/dags and can run a task. Not a pipeline: delete it once a real DAG
exists."""

from __future__ import annotations

import pendulum
from airflow.sdk import dag, task


@dag(
    dag_id="riffle_smoke",
    schedule=None,
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    tags=["smoke"],
)
def riffle_smoke():
    @task
    def hello() -> str:
        return "airflow is running"

    hello()


riffle_smoke()
