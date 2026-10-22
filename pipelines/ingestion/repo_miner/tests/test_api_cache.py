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


def test_the_heartbeat_sends_everything_that_changed_every_15_minutes(tmp_path, monkeypatch):
    import build_dataset
    monkeypatch.chdir(tmp_path)

    class A:
        out = "d.parquet"
        shard = "1/2"
        split_id = "x"
        health_dir = "health"
    monkeypatch.setattr(build_dataset, "_SYNC", {"args": A(), "repos": {"a/b": {"state": "mining", "rows": 1}},
                                                 "counts": {}, "beat": 0})
    monkeypatch.setattr(build_dataset, "_run_settings", lambda a: {})
    monkeypatch.setattr(build_dataset, "_selected_count", lambda r: 5)
    calls = {"status": 0, "sync": 0}
    monkeypatch.setattr(build_dataset.sync, "put_status", lambda p, s: calls.__setitem__("status", calls["status"] + 1))

    def fake_sync(*a, **k):
        calls["sync"] += 1
        return {"sent": 0, "skipped": 3, "failed": [], "stopped": None}
    monkeypatch.setattr(build_dataset.sync, "sync", fake_sync)
    build_dataset._sync_beat(True)
    assert calls == {"status": 1, "sync": 1}             # the first beat sends the lot
    build_dataset._SYNC["beat"] = 0
    build_dataset._sync_beat(True)
    assert calls["sync"] == 1                            # a status beat inside 15 minutes sends status only
    build_dataset._SYNC["log_beat"] -= build_dataset.LOG_BEAT_S
    build_dataset._sync_beat(True)
    assert calls == {"status": 3, "sync": 2}


def test_a_heartbeat_sync_leaves_out_files_it_cannot_finish(tmp_path, monkeypatch):
    import sync
    monkeypatch.setenv("RIFFLE_SYNC_URL", "http://store.invalid")
    monkeypatch.setenv("RIFFLE_SYNC_TOKEN", "rw_x")
    monkeypatch.chdir(tmp_path)
    out = str(tmp_path / "d.parquet")
    (tmp_path / "d.parquet.partial.jsonl").write_bytes(b"x" * 5000)       # the big one
    (tmp_path / "run.log").write_bytes(b"small")
    sent = []
    monkeypatch.setattr(sync, "_put", lambda url, tok, key, body, size, sha, timeout: sent.append(key))
    res = sync.sync(out, "p", {}, logs=[str(tmp_path / "run.log")], state_path=str(tmp_path / "s.json"),
                    max_bytes=1000)
    assert any(k.endswith("logs/run.log") for k in sent)
    assert not any("partial" in k for k in sent) and res["failed"] == []
    sent.clear()
    sync.sync(out, "p", {}, logs=[str(tmp_path / "run.log")], state_path=str(tmp_path / "s.json"))   # a repo's end: no cap
    assert any("partial" in k for k in sent)


def test_graphql_is_left_alone_once_its_quota_is_gone(tmp_path, monkeypatch):
    calls = []

    def fake_get(self, path, params=None, body=None):
        calls.append(path)
        if path == "/graphql":
            return 200, {"errors": [{"type": "RATE_LIMIT", "code": "graphql_rate_limit",
                                     "message": "API rate limit already exceeded"}]}, {}
        return 200, {"resources": {"graphql": {"reset": __import__("time").time() + 600}}}, {}
    monkeypatch.setattr(github_api.GitHubAPI, "_get", fake_get)
    api = _api(tmp_path, monkeypatch)
    api.token = "t"
    assert api.commit_counts([1, 2, 3]) == {1: None, 2: None, 3: None}      # REST will read them
    n = len(calls)
    assert api.review_events_batch([1, 2]) == {1: None, 2: None}
    assert api.check_conclusion("abc") is not None
    assert [c for c in calls[n:] if c == "/graphql"] == []                  # no more GraphQL until it resets
    assert api._gql_blocked_until > __import__("time").time() + 500


def test_a_bulk_review_read_stops_when_the_quota_runs_low(tmp_path, monkeypatch):
    asked = []

    def fake_get(self, path, params=None, body=None):
        asked.append(body["query"])
        return 200, {"data": {"rateLimit": {"remaining": 1500}, "repository": {}}}, {}
    monkeypatch.setattr(github_api.GitHubAPI, "_get", fake_get)
    api = _api(tmp_path, monkeypatch)
    api.token = "t"
    api.review_events_batch(list(range(1, 41)), reserve=2000)
    assert len(asked) == 1                                                  # 4 batches of 10 were wanted; it stopped after one
