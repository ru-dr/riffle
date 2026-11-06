"""The DAGs' shape. Needs Airflow, so it skips on a machine without it and runs in the Airflow image:

  docker compose exec airflow-scheduler python -m pytest /opt/riffle/data_pipeline/tests/test_dag_structure.py
"""

import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("airflow")

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("RIFFLE_PIPELINE_ROOT", str(ROOT))
sys.path.insert(0, str(ROOT / "dags"))

import riffle_data_pipeline as module  # noqa: E402

STAGES = {"acquire", "validate", "preprocess", "features", "slices"}


def edges(dag):
    return {(task.task_id, child) for task in dag.tasks for child in task.downstream_task_ids}


def test_both_dags_have_the_same_tasks():
    for dag in (module.riffle_data_pipeline, module.riffle_data_pipeline_sequential):
        assert {t.task_id for t in dag.tasks} == STAGES | {"alert_on_anomalies"}


def test_parallel_dag_runs_validate_and_preprocess_side_by_side():
    found = edges(module.riffle_data_pipeline)
    assert ("acquire", "validate") in found and ("acquire", "preprocess") in found
    assert ("validate", "preprocess") not in found
    assert {("preprocess", "features"), ("validate", "features")} <= found  # features need validated data
    assert ("validate", "alert_on_anomalies") in found and ("features", "slices") in found


def test_sequential_dag_is_a_chain():
    found = edges(module.riffle_data_pipeline_sequential)
    assert {("acquire", "validate"), ("validate", "preprocess"), ("preprocess", "features")} <= found
    assert ("acquire", "preprocess") not in found


def test_only_the_download_is_retried_and_every_failure_alerts():
    dag = module.riffle_data_pipeline
    assert dag.get_task("acquire").retries == 2
    assert all(dag.get_task(s).retries == 0 for s in STAGES - {"acquire"})
    assert all(t.on_failure_callback for t in dag.tasks)


def test_the_anomaly_alert_runs_even_when_validate_fails():
    assert module.riffle_data_pipeline.get_task("alert_on_anomalies").trigger_rule.value == "all_done"


def test_one_run_at_a_time_and_no_backfill():
    for dag in (module.riffle_data_pipeline, module.riffle_data_pipeline_sequential):
        assert dag.max_active_runs == 1 and dag.catchup is False
