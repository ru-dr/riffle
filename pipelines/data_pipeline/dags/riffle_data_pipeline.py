"""The data pipeline as Airflow DAGs: acquire, validate, preprocess, features, and an alert on anomalies.

Each task runs the same `python -m scripts.<stage>` command DVC runs (`dvc repro` is the
reproducible path on a laptop; Airflow is the scheduled one). The stages run in a separate
virtualenv inside the Airflow image so their packages (pandas, Great Expectations) cannot
conflict with Airflow's own.

Two DAGs share one definition, so the Gantt chart can compare them:
  riffle_data_pipeline             validate and preprocess run side by side (both only read the raw data);
                                   features waits for both, so unvalidated data is never turned into features
  riffle_data_pipeline_sequential  the same tasks one after another; the baseline for the comparison

Every failed task sends an alert (Slack or email, see scripts/alerts.py). After validate, any
anomaly, warning or error, is sent too; a warning does not stop the pipeline.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import timedelta
from pathlib import Path

import pendulum
from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG, task

ROOT = Path(os.environ.get("RIFFLE_PIPELINE_ROOT", "/opt/riffle/data_pipeline"))
PYTHON = os.environ.get("RIFFLE_PIPELINE_PYTHON", "/home/airflow/riffle-venv/bin/python")
AIDEV_SOURCE = os.environ.get("RIFFLE_AIDEV_SOURCE", "/opt/riffle/aidev")

sys.path.insert(0, str(ROOT))  # the alert helpers are standard-library only, so Airflow's Python can import them
from scripts import alerts  # noqa: E402

log = logging.getLogger(__name__)


def stage(name: str, **kwargs) -> BashOperator:
    """Run one pipeline stage. The AIDev package is on PYTHONPATH rather than installed."""
    return BashOperator(
        task_id=name,
        bash_command=f"cd {ROOT} && {PYTHON} -m scripts.{name}",
        env={"PYTHONPATH": AIDEV_SOURCE},
        append_env=True,
        **kwargs,
    )


def build(dag_id: str, parallel: bool, schedule: str | None) -> DAG:
    with DAG(
        dag_id=dag_id,
        description="AIDev: acquire, validate, preprocess, features, alert",
        schedule=schedule,
        start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
        catchup=False,
        max_active_runs=1,
        default_args={
            "owner": "data-pipeline",
            "retries": 0,
            "execution_timeout": timedelta(hours=1),
            "on_failure_callback": alerts.failure_callback,
        },
        tags=["data-pipeline", "aidev"],
    ) as dag:
        # Only the download touches the network, so only it is retried.
        acquire = stage("acquire", retries=2, retry_delay=timedelta(minutes=1), retry_exponential_backoff=True)
        validate = stage("validate")
        preprocess = stage("preprocess")
        features = stage("features")

        @task(task_id="alert_on_anomalies", trigger_rule="all_done")
        def alert_on_anomalies() -> int:
            """Send what validate found. Runs even if validate failed, so errors are reported too."""
            report = ROOT / "reports" / "anomalies.json"
            try:
                summary = alerts.summarise_anomalies(report)
            except (OSError, ValueError) as error:
                log.warning("no readable anomaly report at %s: %s", report, error)
                return 0
            if summary is None:
                log.info("no anomalies to report")
                return 0
            alerts.send_alert(*summary)
            return 1

        alert = alert_on_anomalies()

        acquire >> validate
        if parallel:
            acquire >> preprocess
        else:
            validate >> preprocess
        # Features are only built from data that passed validation. This also makes a failed validate
        # fail the run: Airflow judges a run by its last tasks, and a skipped last task counts as failed.
        [validate, preprocess] >> features
        validate >> alert
    return dag


riffle_data_pipeline = build("riffle_data_pipeline", parallel=True, schedule="@weekly")
riffle_data_pipeline_sequential = build("riffle_data_pipeline_sequential", parallel=False, schedule=None)
