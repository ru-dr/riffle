"""Force-push lookups (open snapshots) report progress and keep each answer on disk: a restart of a 43,000-PR repo does
not repeat hours of GraphQL calls, and an answer that was not read is never remembered."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import github_api  # noqa: E402


def _api(tmp_path, monkeypatch):
    monkeypatch.delenv("RIFFLE_NO_API_CACHE", raising=False)
    st = config.Settings()
    st.ci_cache_dir = str(tmp_path)
    api = github_api.GitHubAPI("o", "r", st)
    api.token = "t"
    return api


def _fake(calls, unknown=()):
    def get(self, path, params=None, body=None):
        q = body["query"]
        calls.append(q)
        repo = {}
        for n in range(1, 200):
            if f"p{n}:" in q and n not in unknown:
                repo[f"p{n}"] = {"timelineItems": {"pageInfo": {"hasNextPage": False},
                                                   "nodes": [{"createdAt": f"2026-01-{n % 28 + 1:02d}T00:00:00Z",
                                                              "beforeCommit": {"oid": f"sha{n}"}}] if n % 2 else []}}
        return 200, {"data": {"repository": repo}}, {}
    return get


def test_a_restart_asks_github_only_for_what_it_does_not_have(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(github_api.GitHubAPI, "_get", _fake(calls))
    nums = list(range(1, 61))
    first = _api(tmp_path, monkeypatch).force_pushes(nums)
    assert len(calls) == 3                                           # 60 PRs, 25 a call
    assert first[1] == [("2026-01-02T00:00:00Z", "sha1")] and first[2] == []
    calls.clear()
    again = _api(tmp_path, monkeypatch).force_pushes(nums + [61])
    assert len(calls) == 1 and "p61:" in calls[0] and "p5:" not in calls[0]
    assert {n: again[n] for n in nums} == {n: first[n] for n in nums}


def test_an_unknown_answer_is_not_remembered(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(github_api.GitHubAPI, "_get", _fake(calls, unknown={3}))
    a = _api(tmp_path, monkeypatch)
    assert a.force_pushes([1, 2, 3])[3] is None
    calls.clear()
    monkeypatch.setattr(github_api.GitHubAPI, "_get", _fake(calls))
    out = _api(tmp_path, monkeypatch).force_pushes([1, 2, 3])
    assert len(calls) == 1 and "p3:" in calls[0] and "p1:" not in calls[0]   # only the one that was not read
    assert out[3] == [("2026-01-04T00:00:00Z", "sha3")]


def test_progress_is_printed_and_traced(tmp_path, monkeypatch, capsys):
    import runlog
    events = []
    monkeypatch.setattr(runlog, "info", lambda stage, msg, /, **f: events.append((stage, msg, f)))
    monkeypatch.setattr(github_api.GitHubAPI, "_get", _fake([]))
    _api(tmp_path, monkeypatch).force_pushes(list(range(1, 51)))
    err = capsys.readouterr().err
    assert "[open-snapshot] force-push lookups 50/50 PRs" in err
    last = [f for s, m, f in events if (s, m) == ("stage", "progress")][-1]
    assert last["phase"] == "force-pushes" and last["done"] == 50 and last["total"] == 50 and last["eta_s"] == 0
