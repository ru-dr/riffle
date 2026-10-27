"""publish.py: contract check on merged tables, and the DVC steps it runs (dvc itself is faked)."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import publish  # noqa: E402

pd = pytest.importorskip("pandas")
pytest.importorskip("pyarrow")


def _contracts(tmp_path):
    d = tmp_path / "contracts"
    d.mkdir()
    for name, cols in {"mined_dataset": ["a", "b"], "mined_snapshot": ["s"], "mined_commit_features": ["c"]}.items():
        (d / f"{name}.schema.json").write_text(json.dumps({"properties": {c: {} for c in cols}}))
    return str(d)


def _tables(tmp_path, dataset_cols=("a", "b")):
    out = tmp_path / "out"
    out.mkdir()
    pd.DataFrame({c: [1] for c in dataset_cols}).to_parquet(out / "d_pc1of1_merged.parquet")
    pd.DataFrame({"s": [1]}).to_parquet(out / "d_pc1of1_merged_snapshots.parquet")
    pd.DataFrame({"c": [1]}).to_parquet(out / "d_pc1of1_merged_commit_features.parquet")
    (out / "d_pc1of1_merged.merge.json").write_text(json.dumps({"miner_version": ["7.0.1"]}))
    return str(out)


def test_matching_tables_pass(tmp_path):
    assert publish.check(_tables(tmp_path), "7.0.1", _contracts(tmp_path)) == []


def test_missing_extra_and_version_are_reported(tmp_path):
    problems = publish.check(_tables(tmp_path, ("a", "z")), "7.0.0", _contracts(tmp_path))
    text = " ".join(problems)
    assert "missing columns ['b']" in text and "['z']" in text and "7.0.1" in text and "wanted '7.0.0'" in text


def test_missing_table_is_an_error(tmp_path):
    out = _tables(tmp_path)
    os.remove(os.path.join(out, "d_pc1of1_merged_snapshots.parquet"))
    with pytest.raises(ValueError):
        publish.check(out, None, _contracts(tmp_path))


def test_push_runs_dvc_in_order_and_copies_files(tmp_path):
    out, ws, calls = _tables(tmp_path), str(tmp_path / "ws"), []
    os.makedirs(ws)

    def fake(cmd, cwd, check):
        calls.append(cmd[:3])
        if cmd[:2] == ["dvc", "add"]:
            (tmp_path / "ws" / "mined.dvc").write_text("outs: []\n")
    text = publish.push(out, ws, "gs://b/r", "7.0.1", run=fake)
    assert [c[1] for c in calls] == ["init", "remote", "add", "push"] and text == "outs: []\n"
    assert len(os.listdir(os.path.join(ws, "mined", "7.0.1"))) == 4


def test_tables_without_a_manifest_cannot_vouch_for_their_version(tmp_path):
    out = _tables(tmp_path)
    os.remove(os.path.join(out, "d_pc1of1_merged.merge.json"))
    contracts = _contracts(tmp_path)
    problems = publish.check(out, "7.0.1", contracts)
    assert any("unknown" in p for p in problems)                      # unobserved is not a pass
    assert publish.check(out, None, contracts) == []                   # asking nothing about the version is allowed


def test_push_versions_only_this_runs_files(tmp_path):
    out, ws = _tables(tmp_path), str(tmp_path / "ws")
    os.makedirs(os.path.join(ws, "mined", "7.0.1"))
    open(os.path.join(ws, "mined", "7.0.1", "leftover_from_an_earlier_push.parquet"), "w").write("x")

    def fake(cmd, cwd, check):
        if cmd[:2] == ["dvc", "add"]:
            open(os.path.join(cwd, "mined.dvc"), "w").write("outs: []\n")
    publish.push(out, ws, "gs://b/r", "7.0.1", run=fake)
    assert "leftover_from_an_earlier_push.parquet" not in os.listdir(os.path.join(ws, "mined", "7.0.1"))
