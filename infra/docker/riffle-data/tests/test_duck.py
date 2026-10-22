"""duck.py: the three views over PCs' parquet files, finished PCs only by default."""
from __future__ import annotations

import json
import os
import sys

import pytest

pytest.importorskip("duckdb")
pd = pytest.importorskip("pandas")
pytest.importorskip("pyarrow")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))

import duck  # noqa: E402


def _pc(root, pc, n, reason, rows):
    d = root / pc / "6.6.0" / "abc"
    (d / "data").mkdir(parents=True)
    pd.DataFrame({"repo": [f"o/{pc}"] * rows, "pr": range(rows)}).to_parquet(d / "data" / f"dataset_pc{n}of2.parquet")
    pd.DataFrame({"repo": [f"o/{pc}"] * rows * 2, "snap": range(rows * 2)}).to_parquet(
        d / "data" / f"dataset_pc{n}of2_snapshots.parquet")
    (d / "run.json").write_text(json.dumps({"reason": reason}))


@pytest.fixture
def store(tmp_path):
    _pc(tmp_path, "pc1", 1, "batch done", 3)
    _pc(tmp_path, "pc2", 2, "repo done", 5)
    return duck.LocalSource(str(tmp_path))


def test_views_cover_finished_pcs_only_by_default(store):
    con, _ = duck.connect(store, "6.6.0", "abc")
    assert con.execute("select count(*) from mined_dataset").fetchone()[0] == 3
    assert con.execute("select count(*) from mined_snapshot").fetchone()[0] == 6
    assert con.execute("select distinct repo from mined_dataset").fetchall() == [("o/pc1",)]
    assert "pc1" in con.execute("select filename from mined_dataset limit 1").fetchone()[0]
    assert "mined_commit_features" not in [r[0] for r in con.execute("select view_name from duckdb_views() where not internal").fetchall()]


def test_include_unfinished_adds_the_other_pc(store):
    con, _ = duck.connect(store, "6.6.0", "abc", include_unfinished=True)
    assert con.execute("select count(*) from mined_dataset").fetchone()[0] == 8


def test_cli_prints_a_query_and_can_write_a_db(store, capsys, tmp_path):
    db = str(tmp_path / "mined.duckdb")
    assert duck.main(["--local", store.root, "--version", "6.6.0", "--split", "abc", "--db", db,
                      "-c", "select count(*) as n from mined_dataset"]) == 0
    assert capsys.readouterr().out.split() == ["n", "3"]
    assert os.path.isfile(db)
    assert duck.main(["--local", store.root, "--version", "9.9.9", "--split", "abc"]) == 1
