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
