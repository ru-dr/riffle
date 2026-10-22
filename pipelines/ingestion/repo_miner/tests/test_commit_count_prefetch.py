"""The rebase resolver reads PR commit counts in one GraphQL pass, not one REST call per PR."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import gitio  # noqa: E402
import mine_repo  # noqa: E402


class FakeApi:
    token = "t"

    def __init__(self):
        self.graphql_calls = 0
        self.rest_calls = 0

    def repo_merge_methods(self):
        return {"rebase": True, "squash": False}

    def commit_counts(self, numbers):
        self.graphql_calls += 1
        return {n: (None if n == 3 else 2) for n in numbers}

    def pr_commit_count(self, number):
        self.rest_calls += 1
        return 2


def test_counts_are_prefetched_once_and_rest_fills_the_gaps(monkeypatch):
    monkeypatch.setattr(gitio, "commit_parents", lambda repo, sha: ["a"])
    monkeypatch.setattr(gitio, "rev_parse_safe", lambda repo, rev: "base")
    prs = [{"number": n, "merge_commit_sha": "m%d" % n} for n in (1, 2, 3)]
    api = FakeApi()
    resolve = mine_repo._make_base_resolver("repo", api, prs, config.Settings())
    for pr in prs:
        assert resolve(pr, "head") == "base"
    assert api.graphql_calls == 1
    assert api.rest_calls == 1                 # only PR 3, which GraphQL could not read


def test_review_batch_matches_review_events(monkeypatch):
    import github_api
    api = github_api.GitHubAPI("o", "r", config.Settings())
    api.token = "t"
    node = {"reviews": {"pageInfo": {"hasNextPage": False}, "nodes": [
                {"state": "APPROVED", "submittedAt": "2026-01-02T00:00:00Z", "author": {"__typename": "User", "login": "a"}},
                {"state": "CHANGES_REQUESTED", "submittedAt": "2026-01-01T00:00:00Z", "author": {"__typename": "User", "login": "b"}},
                {"state": "COMMENTED", "submittedAt": "2026-01-03T00:00:00Z", "author": {"__typename": "Bot", "login": "ci"}},
                {"state": "PENDING", "submittedAt": None, "author": None}]},
            "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": [{"comments": {
                "pageInfo": {"hasNextPage": False}, "nodes": [
                    {"createdAt": "2026-01-02T05:00:00Z", "author": {"__typename": "User", "login": "a"}},
                    {"createdAt": "2026-01-02T04:00:00Z", "author": None}]}}]}}
    big = {"reviews": {"pageInfo": {"hasNextPage": True}, "nodes": []},
           "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}}
    monkeypatch.setattr(api, "_get", lambda path, params=None, body=None:
                        (200, {"data": {"repository": {"p1": node, "p2": big}}}, {}))
    got = api.review_events_batch([1, 2])
    assert got[1] == {"comments": ["2026-01-02T04:00:00Z", "2026-01-02T05:00:00Z"],
                      "changes_requested": ["2026-01-01T00:00:00Z"],
                      "rounds": ["2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z"]}
    assert got[2] is None                       # does not fit one page: REST reads it
    api.prefetch_reviews([1, 2])
    assert api.review_events(1) == got[1]       # answered from the cache, no REST call


def test_a_second_interrupt_right_after_the_first_is_ignored():
    import signal
    import time
    old = signal.getsignal(signal.SIGINT)
    try:
        config.stop_on_break()
        h = signal.getsignal(signal.SIGINT)
        try:
            h(signal.SIGINT, None)
            raised = False
        except KeyboardInterrupt:
            raised = True
        assert raised                          # the first one stops the run
        h(signal.SIGINT, None)                 # the second, a moment later, does nothing
        time.sleep(0)                          # (a person's later ctrl+c is a new interrupt after 5 s)
    finally:
        signal.signal(signal.SIGINT, old)


def test_the_batch_status_carries_the_quota_wait(tmp_path, monkeypatch):
    import time
    import build_dataset
    monkeypatch.chdir(tmp_path)
    os.makedirs("ci_cache")
    open("ci_cache/.rate_wait", "w").write(str(int(time.time()) + 600))

    class A:
        out = "d.parquet"
        shard = "1/2"
        split_id = "x"
    monkeypatch.setattr(build_dataset, "_SYNC", {"args": A(), "repos": {}, "counts": {}})
    monkeypatch.setattr(build_dataset, "_run_settings", lambda a: {})
    assert build_dataset._sync_status("heartbeat")["rate_wait_until"]
    open("ci_cache/.rate_wait", "w").write(str(int(time.time()) - 5))
    assert build_dataset._sync_status("heartbeat")["rate_wait_until"] is None


def test_a_stopped_batch_reports_no_repo_as_mining(tmp_path, monkeypatch):
    import build_dataset
    monkeypatch.chdir(tmp_path)

    class A:
        out = "d.parquet"
        shard = "1/2"
        split_id = "x"
    repos = {"a/b": {"state": "mining", "rows": 5, "selected": 20}, "c/d": {"state": "pending"}}
    monkeypatch.setattr(build_dataset, "_SYNC", {"args": A(), "repos": repos, "counts": {}})
    monkeypatch.setattr(build_dataset, "_run_settings", lambda a: {})
    monkeypatch.setattr(build_dataset, "_selected_count", lambda r: 20)
    live = build_dataset._sync_status("heartbeat")
    assert live["current"] == "a/b" and live["repos"]["a/b"]["state"] == "mining"
    stopped = build_dataset._sync_status("stopped")
    assert stopped["current"] is None and stopped["repos"]["a/b"]["state"] == "stopped"
    assert stopped["prs_remaining"] == live["prs_remaining"]       # the repo's unfinished PRs still count
    assert repos["a/b"]["state"] == "mining"                       # the live record is not touched
