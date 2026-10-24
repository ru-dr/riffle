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


class _Resp:
    """An urlopen response whose body is cut off mid-read when `cut` (an IncompleteRead)."""
    status = 200
    headers = {}

    def __init__(self, cut):
        self.cut = cut

    def read(self):
        if self.cut:
            import http.client
            raise http.client.IncompleteRead(b"x", 5)
        return b'{"workflow_runs": []}'

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _real_api():
    import config
    return github_api.GitHubAPI("o", "r", config.Settings())


def test_persistent_get_retries_a_cut_off_body(monkeypatch):
    # the 6.13.1 runs on nuxt/nuxt and remix-run/react-router died here: a ~1 MB
    # runs page cut mid-read raised IncompleteRead out of the whole repo
    calls = []

    def fake_open(req, timeout=None):
        calls.append(1)
        return _Resp(cut=len(calls) < 3)

    monkeypatch.setattr(github_api.urllib.request, "urlopen", fake_open)
    monkeypatch.setattr(github_api.time, "sleep", lambda s: None)
    status, data = _real_api()._get_persistent("/repos/o/r/actions/runs", {"page": 1})
    assert (status, data) == (200, {"workflow_runs": []}) and len(calls) == 3


def test_persistent_get_gives_up_without_raising(monkeypatch):
    monkeypatch.setattr(github_api.urllib.request, "urlopen", lambda req, timeout=None: _Resp(cut=True))
    monkeypatch.setattr(github_api.time, "sleep", lambda s: None)
    assert _real_api()._get_persistent("/repos/o/r/actions/runs") == (0, None)
