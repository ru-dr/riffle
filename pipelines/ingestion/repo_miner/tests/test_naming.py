"""Auto-naming of a sharded run's dataset and logs."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import naming  # noqa: E402


def test_auto_tag_for_sharded_runs_only():
    assert naming.tagged_out("dataset.parquet", "1/3") == "dataset_pc1of3.parquet"
    assert naming.tagged_out("out/data.jsonl", "2/3") == os.path.join("out", "data_pc2of3.jsonl")
    assert naming.tagged_out("dataset.parquet", None) == "dataset.parquet"
    assert naming.tagged_out("dataset.parquet", "1/1") == "dataset.parquet"
    assert naming.tagged_out("dataset.parquet", "1/3", auto=False) == "dataset.parquet"


def test_idempotent_and_malformed_shard():
    once = naming.tagged_out("dataset.parquet", "1/3", prefix="vite")
    assert naming.tagged_out(once, "1/3", prefix="vite") == once
    assert naming.tagged_out("dataset.parquet", "x/y") == "dataset.parquet"
    assert naming.tagged_out("dataset.parquet", "4/3") == "dataset.parquet"


def test_prefix_postfix_and_placeholders():
    assert naming.tagged_out("d.parquet", "1/3", prefix="web_") == "web_d_pc1of3.parquet"
    assert naming.tagged_out("d.parquet", "1/3", prefix="web") == "web_d_pc1of3.parquet"
    assert naming.tagged_out("d.parquet", "1/3", postfix="lab{pc}") == "d_lab1.parquet"   # replaces auto tag
    assert naming.tagged_out("d.parquet", None, postfix="run 2") == "d_run-2.parquet"
    host = naming._host()
    assert naming.tagged_out("d.parquet", "2/3", postfix="{host}-{pc}of{n}") == f"d_{host}-2of3.parquet"
    assert naming.tagged_out("d.parquet", None, postfix="{nope}") == "d_-nope-.parquet"


def test_run_stem_carries_pc_tag():
    assert naming.run_stem("2/3").endswith("-pc2of3")
    assert not naming.run_stem(None).endswith("of3") and naming.run_stem(None).startswith("run-")


def test_snapshot_outs_follow_the_dataset_name():
    assert naming.snapshot_outs("dataset_pc1of3.parquet") == (
        "dataset_pc1of3_snapshots.parquet", "dataset_pc1of3_commit_features.parquet")
    assert naming.snapshot_outs(os.path.join("out", "d.jsonl")) == (
        os.path.join("out", "d_snapshots.jsonl"), os.path.join("out", "d_commit_features.jsonl"))
