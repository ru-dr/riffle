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
