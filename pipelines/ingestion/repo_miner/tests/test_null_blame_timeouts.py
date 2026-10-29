"""null_blame_timeouts: a PR named in a trace's blame timeout gets its blame columns nulled, others not."""
from __future__ import annotations

import json
import os
import sys

import pytest

pd = pytest.importorskip("pandas")
pytest.importorskip("pyarrow")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import null_blame_timeouts as nb  # noqa: E402


def test_only_named_prs_are_nulled(tmp_path, monkeypatch):
    trace = tmp_path / "run.trace.jsonl"
    ev = {"stage": "git", "msg": "call", "exc": "TimeoutExpired", "cmd": "blame"}
    other = dict(ev, cmd="fetch")
    trace.write_text("\n".join(json.dumps(e) for e in [
        dict(ev, repo="Acme/Web", pr=2), dict(other, repo="Acme/Web", pr=1), dict(ev)]) + "\n")
    df = pd.DataFrame({"repo": ["acme/web"] * 3, "pr_number": [1, 2, 3],
                       "max_mod_lines_distinct_authors": [0, 0, 4],
                       "max_hunk_fix_proximity": [0.0, 0.0, 1.5], "LA": [5, 6, 7]})
    ds = tmp_path / "d.parquet"
    df.to_parquet(ds)
    monkeypatch.setattr(sys, "argv", ["x", "--dataset", str(ds), "--traces", str(trace), "--apply"])
    assert nb.main() == 0
    out = pd.read_parquet(ds)
    assert out["max_mod_lines_distinct_authors"].isna().tolist() == [False, True, False]
    assert out["max_hunk_fix_proximity"].isna().tolist() == [False, True, False]
    assert out["LA"].tolist() == [5, 6, 7]
    assert os.path.exists(str(ds) + ".pre-blame-null.bak")
