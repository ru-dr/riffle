"""
CI-history pre-pass resume: pages are checkpointed, a stopped walk resumes at
the next page, and a walk that ended on a failed page is never cached as
complete.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import github_api  # noqa: E402


def _run(i: int) -> dict:
    return {"head_sha": f"sha{i}", "event": "push", "name": "build", "conclusion": "success"}


def _api(pages: dict[int, list | None], calls: list):
    """A GitHubAPI whose workflow-runs endpoint serves `pages` (None = failure)."""
    api = object.__new__(github_api.GitHubAPI)
    api.owner, api.repo = "o", "r"
    api._is_optional = lambda name: False

    def get(path, params):
        calls.append(params["page"])
        runs = pages.get(params["page"])
        return (200, {"workflow_runs": runs}) if runs is not None else (500, None)
    api._get_persistent = get
    return api


def test_failed_page_is_not_cached_and_rerun_resumes(tmp_path):
    cache = str(tmp_path / "o_r.json")
    full = {p: [_run(p * 100 + i) for i in range(100)] for p in range(1, 12)}
    full[12] = [_run(9999)]                                  # short last page
    calls: list = []
    broken = dict(full)
    broken[11] = None                                        # page 11 fails
    first = _api(broken, calls).build_ci_conclusion_map(cache)
    assert len(first) == 1000
    assert not os.path.isfile(cache)                         # truncated map not cached
    with open(cache + ".pages.json") as fh:
        assert json.load(fh)["next_page"] == 11

    calls.clear()
    second = _api(full, calls).build_ci_conclusion_map(cache)
    assert calls == [11, 12]                                 # resumed, not from page 1
    assert len(second) == 1101
    assert os.path.isfile(cache)
    assert not os.path.isfile(cache + ".pages.json")
