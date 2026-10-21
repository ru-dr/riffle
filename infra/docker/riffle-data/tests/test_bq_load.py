"""bq_load.py: which bucket files go to which BigQuery table. No BigQuery calls."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))

import bq_load  # noqa: E402

P = "6.6.0/abc/data/"
KEYS = [f"pc1/{P}dataset_pc1of2.parquet", f"pc1/{P}dataset_pc1of2_snapshots.parquet",
        f"pc1/{P}dataset_pc1of2_commit_features.parquet", f"pc1/{P}dataset_pc1of2.parquet.partial.jsonl",
        f"pc1/{P}dataset_pc1of2.shard.json", f"pc2/{P}dataset_pc2of2.parquet",
        f"pc3/{P}dataset_pc3of3.jsonl", "pc1/6.5.0/abc/data/dataset_pc1of2.parquet",
        "pc1/6.6.0/other/data/dataset_pc1of2.parquet", "pc1/6.6.0/abc/logs/run-1.log"]


def test_each_file_goes_to_its_own_table():
    assert bq_load.table_for("dataset_pc1of2.parquet") == "mined_dataset"
    assert bq_load.table_for("dataset_pc1of2_snapshots.parquet") == "mined_snapshot"
    assert bq_load.table_for("dataset_pc1of2_commit_features.parquet") == "mined_commit_features"
    for other in ("dataset_pc1of2.shard.json", "dataset_pc1of2.parquet.partial.jsonl", "dataset.jsonl"):
        assert bq_load.table_for(other) is None


def test_plan_takes_one_version_and_split_and_reports_jsonl_only_pcs():
    files, notes = bq_load.plan(KEYS, "6.6.0", "abc")
    assert files["mined_dataset"] == [f"pc1/{P}dataset_pc1of2.parquet", f"pc2/{P}dataset_pc2of2.parquet"]
    assert files["mined_snapshot"] == [f"pc1/{P}dataset_pc1of2_snapshots.parquet"]
    assert files["mined_commit_features"] == [f"pc1/{P}dataset_pc1of2_commit_features.parquet"]
    assert any("pc3" in n and "JSONL" in n for n in notes)


def test_only_finished_pcs_are_loaded():
    files, _ = bq_load.plan(KEYS, "6.6.0", "abc", finished={"pc2"})
    assert files["mined_dataset"] == [f"pc2/{P}dataset_pc2of2.parquet"]
    assert files["mined_snapshot"] == []
