"""A restarted run reads a PR's timeline, review activity and commit count from disk, not GitHub."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api_cache  # noqa: E402
import config  # noqa: E402
import github_api  # noqa: E402


def _api(tmp_path, monkeypatch):
    monkeypatch.delenv("RIFFLE_NO_API_CACHE", raising=False)
    st = config.Settings()
    st.ci_cache_dir = str(tmp_path)
    return github_api.GitHubAPI("o", "r", st)


def test_a_timeline_is_read_once_across_restarts(tmp_path, monkeypatch):
    calls = []

    def fake_get(self, path, params=None, body=None):
        calls.append(path)
        return 200, [{"event": "labeled", "id": 1}], {}
    monkeypatch.setattr(github_api.GitHubAPI, "_get", fake_get)
    first = _api(tmp_path, monkeypatch).pr_timeline(7)
    second = _api(tmp_path, monkeypatch).pr_timeline(7)       # a new process: a new object, the same file
    assert first == second == [{"event": "labeled", "id": 1}]
    assert len(calls) == 1


def test_an_unreadable_timeline_is_not_remembered(tmp_path, monkeypatch):
    answers = [(500, None, {}), (200, [{"event": "closed"}], {})]
    monkeypatch.setattr(github_api.GitHubAPI, "_get", lambda self, p, params=None, body=None: answers.pop(0))
    api = _api(tmp_path, monkeypatch)
    assert api.pr_timeline(7) is None
    assert api.pr_timeline(7) == [{"event": "closed"}]        # asked again, not served a gap


def test_old_entries_are_ignored(tmp_path, monkeypatch):
    c = api_cache.ApiCache(str(tmp_path / "x.sqlite"))
    c.put("k", {"a": 1})
    assert c.get("k") == {"a": 1}
    monkeypatch.setattr(api_cache, "MAX_AGE_S", -1)
    assert c.get("k") is None


def test_review_activity_and_commit_counts_are_cached(tmp_path, monkeypatch):
    seen = []

    def fake_get(self, path, params=None, body=None):
        seen.append(path)
        if path.endswith("/reviews"):
            return 200, [{"state": "APPROVED", "submitted_at": "2026-01-01T00:00:00Z", "user": {"login": "a"}}], {}
        if path.endswith("/comments"):
            return 200, [], {}
        return 200, {"commits": 3}, {}
    monkeypatch.setattr(github_api.GitHubAPI, "_get", fake_get)
    a = _api(tmp_path, monkeypatch)
    ev, n = a.review_events(5), a.pr_commit_count(5)
    seen.clear()
    b = _api(tmp_path, monkeypatch)
    assert b.review_events(5) == ev and b.pr_commit_count(5) == n == 3
    assert seen == []


def _gql_commit(suites, contexts=()):
    return {"data": {"repository": {"object": {"checkSuites": {"pageInfo": {"hasNextPage": False}, "nodes": suites},
                                               "status": {"contexts": list(contexts)}}}}}


def _suite(slug, event, runs, more=False):
    return {"databaseId": 1, "app": {"slug": slug}, "workflowRun": {"event": event} if event else None,
            "checkRuns": {"pageInfo": {"hasNextPage": more}, "nodes": runs}}


def _run(name, conclusion, at, i):
    return {"databaseId": i, "name": name, "conclusion": conclusion, "completedAt": at, "startedAt": at}


def test_checks_through_graphql_keep_reruns_and_drop_automation(tmp_path, monkeypatch):
    payload = _gql_commit([
        _suite("github-actions", "PUSH", [_run("Test", "FAILURE", "2026-01-01T00:00:00Z", 1),
                                           _run("Test", "SUCCESS", "2026-01-01T01:00:00Z", 2)]),
        _suite("github-actions", "ISSUES", [_run("triage", "FAILURE", "2026-01-01T00:00:00Z", 3)]),
        _suite("circleci", None, [_run("ci/build", "SUCCESS", "2026-01-01T00:00:00Z", 4)]),
    ], [{"context": "legacy/ci", "state": "ERROR"}])
    monkeypatch.setattr(github_api.GitHubAPI, "_get", lambda self, p, params=None, body=None: (200, payload, {}))
    api = _api(tmp_path, monkeypatch)
    api.token = "t"
    out = api.check_conclusion("abc", ("push",))
    names = {c["name"]: c for c in out["checks"]}
    assert "triage" not in names                      # an issues-workflow run is not this commit's CI
    assert names["test"]["rerun_pass"] is True        # failed, then passed on a rerun
    assert names["legacy/ci"]["conclusion"] == "failure"
    assert [c["name"] for c in out["checks"]] == sorted(names)
    assert out["required"] == "failure"               # the legacy status failed


def test_a_full_page_of_checks_is_read_through_rest(tmp_path, monkeypatch):
    seen = []

    def fake_get(self, path, params=None, body=None):
        seen.append(path)
        if path == "/graphql":
            return 200, _gql_commit([_suite("circleci", None, [], more=True)]), {}
        if path.endswith("/check-runs"):
            return 200, {"check_runs": [{"conclusion": "success", "name": "x", "id": 1,
                                         "completed_at": "2026-01-01T00:00:00Z", "app": {"slug": "circleci"}}]}, {}
        return 200, {"statuses": []}, {}
    monkeypatch.setattr(github_api.GitHubAPI, "_get", fake_get)
    api = _api(tmp_path, monkeypatch)
    api.token = "t"
    assert api.check_conclusion("abc")["required"] == "success"
    assert any(p.endswith("/check-runs") for p in seen)
