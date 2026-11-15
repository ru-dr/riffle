"""outcome/model_gates.py: a model that meets every limit passes; each kind of failure blocks it."""
from __future__ import annotations

import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "outcome"))

import model_gates as G  # noqa: E402

CHECKS = ["Prediction file is complete and valid", "Model reads no banned columns", "Beats the base rate overall",
          "Beats the base rate in every main group",
          "Probabilities are honest (calibration error <= 0.03, slopes 0.8 to 1.2)",
          "'Likely to go nowhere' list beats PR age only", "Fast enough (95% of PRs scored within 200 ms)",
          "Trained with 3 or more seeds"]


def _eval(root, skill=0.21, calib=0.014, ff_new=0.34, ff_ret=0.10, fail=None):
    for split in G.SPLITS:
        os.makedirs(os.path.join(root, split, "bias"), exist_ok=True)
        checks = [dict(check=c, result="FAIL" if c == fail or c.startswith("'Likely") else "PASS") for c in CHECKS]
        s = dict(metrics=dict(skill=skill + (0.05 if split == "by_pr" else 0), calibration_error=calib),
                 latency_ms=dict(p95=0.3), checklist=checks)
        json.dump(s, open(os.path.join(root, split, "summary.json"), "w"))
    pd.DataFrame([dict(slice="author", group="first PR in repo", accuracy=0.59, false_flag_rate=ff_new),
                  dict(slice="author", group="returning", accuracy=0.61, false_flag_rate=ff_ret)]).to_csv(
        os.path.join(root, "by_repo", "bias", "slices.csv"), index=False)
    return root


def _failed(rows):
    return [r["check"] for r in rows if r["result"] == "FAIL"]


def test_a_good_model_passes_and_list_ties_do_not_block(tmp_path):
    rows = G.run_gates(_eval(str(tmp_path / "cur")), _eval(str(tmp_path / "base")))
    assert not _failed(rows)
    assert any(r["result"] == "INFO" for r in rows)


def test_each_kind_of_failure_blocks(tmp_path):
    assert _failed(G.run_gates(_eval(str(tmp_path / "a"), calib=0.05)))
    assert _failed(G.run_gates(_eval(str(tmp_path / "b"), skill=0.10)))
    assert _failed(G.run_gates(_eval(str(tmp_path / "c"), fail="Model reads no banned columns")))
    assert _failed(G.run_gates(_eval(str(tmp_path / "d"), ff_new=0.45)))
    assert _failed(G.run_gates(_eval(str(tmp_path / "e"), ff_new=0.38, ff_ret=0.08)))       # ratio 4.75


def test_regression_against_main_blocks(tmp_path):
    base = _eval(str(tmp_path / "base"), skill=0.22)
    assert "skill change vs main" in _failed(G.run_gates(_eval(str(tmp_path / "cur"), skill=0.21), base))
    worse_bias = _eval(str(tmp_path / "cur2"), skill=0.22, ff_new=0.37)
    assert "newcomer false-flag change vs main" in _failed(G.run_gates(worse_bias, _eval(str(tmp_path / "b2"),
                                                                                         skill=0.22, ff_new=0.34)))


def test_missing_outputs_block(tmp_path):
    os.makedirs(tmp_path / "empty")
    assert _failed(G.run_gates(str(tmp_path / "empty")))
