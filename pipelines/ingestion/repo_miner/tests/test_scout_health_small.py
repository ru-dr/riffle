"""scout: a repo that scored 0.0 outranks one that errored; health: the JSON is written atomically."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import health  # noqa: E402
import scout  # noqa: E402


def test_zero_score_sorts_above_error(monkeypatch):
    class Api:
        def __init__(self, owner, name, st):
            self.owner = owner
        def _get(self, path, params):
            return (200, [], {}) if self.owner == "ok" else (500, None, {})
    monkeypatch.setattr(scout, "GitHubAPI", Api)
    res = scout.scout([("bad", "r", None), ("ok", "r", None)], None)
    assert [r["repo"] for r in res] == ["ok/r", "bad/r"]


def test_health_json_leaves_no_tmp(tmp_path):
    health.report([{"label_merged": True}], "o/r", str(tmp_path))
    assert sorted(os.listdir(tmp_path)) == ["o_r.json"]
