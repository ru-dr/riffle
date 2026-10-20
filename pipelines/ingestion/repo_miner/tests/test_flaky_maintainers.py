"""Flaky-check detection (rules.mining.flaky_detection) and maintainers
confirmed from history (proposal 2.2)."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import flaky  # noqa: E402
import maintainers  # noqa: E402
import mine_repo  # noqa: E402
import snapshots  # noqa: E402
from github_api import GitHubAPI  # noqa: E402
from labels import CI_SIDECAR  # noqa: E402

UTC = timezone.utc
T0 = datetime(2025, 3, 1, tzinfo=UTC)


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---- flaky checks ---------------------------------------------------------------

def _row(day, failing=(), rerun_passes=(), revert=False):
    return {"label_ci_fail": bool(failing), "label_bad": bool(failing) or revert, "label_revert": revert,
            "label_hotfix": False, "ci_fail_flaky_only": False,
            CI_SIDECAR: {"at": iso(T0 + timedelta(days=day)), "failing": list(failing),
                         "rerun_passes": list(rerun_passes)}}


def test_failure_only_from_a_flaky_check_is_not_a_ci_fail():
    st = config.Settings()
    rows = [_row(d, rerun_passes=["tests (ubuntu)"]) for d in (1, 5, 9)]      # 3 rerun-passes
    rows.append(_row(20, failing=["tests (ubuntu)"]))                         # flaky by then
    rows.append(_row(20, failing=["tests (ubuntu)", "build"]))                # build isn't flaky
    rows.append(_row(20, failing=["tests (ubuntu)"], revert=True))
    assert flaky.apply(rows, st) == 2
    assert rows[3]["label_ci_fail"] is False and rows[3]["ci_fail_flaky_only"] is True
    assert rows[3]["label_bad"] is False
    assert rows[4]["label_ci_fail"] is True and rows[4]["ci_fail_flaky_only"] is False
    assert rows[5]["label_ci_fail"] is False and rows[5]["label_bad"] is True   # still reverted


def test_flakiness_counts_only_rerun_passes_before_the_merge():
    st = config.Settings()
    rows = [_row(1, rerun_passes=["t"]), _row(2, rerun_passes=["t"]),
            _row(3, failing=["t"]),                                   # only 2 before it
            _row(4, rerun_passes=["t"]), _row(200, failing=["t"])]    # outside the 90-day lookback
    assert flaky.apply(rows, st) == 0
    st.flaky_detection = False
    assert flaky.apply([_row(1, failing=["t"])], st) == 0


def test_check_runs_mark_rerun_passes():
    class Fake(GitHubAPI):
        def _get(self, path, params=None, body=None):
            if path.endswith("/check-runs"):
                assert (params or {}).get("filter") == "all"
                return 200, {"check_runs": [
                    {"id": 1, "name": "tests", "conclusion": "failure", "completed_at": "2025-01-01T00:00:00Z"},
                    {"id": 2, "name": "tests", "conclusion": "success", "completed_at": "2025-01-01T01:00:00Z"},
                    {"id": 3, "name": "lint", "conclusion": "failure", "completed_at": "2025-01-01T00:00:00Z"},
                    {"id": 4, "name": "codeql", "conclusion": "failure", "completed_at": "2025-01-01T00:00:00Z"},
                ]}, {}
            return 200, {"statuses": []}, {}

    c = Fake("o", "r", config.Settings()).check_conclusion("abc")
    assert c["required"] == "failure"            # lint's latest run failed; tests' latest passed
    by = {x["name"]: x for x in c["checks"]}
    assert by["tests"]["rerun_pass"] is True and by["tests"]["conclusion"] == "success"
    assert by["lint"]["rerun_pass"] is False and "codeql" not in by     # optional check


def test_only_push_workflows_count_as_the_merge_commits_ci():
    """Issue, schedule and comment workflows land check runs on whatever commit was main's head."""
    def run(i, name, suite, conclusion, app="github-actions"):
        return {"id": i, "name": name, "conclusion": conclusion, "check_suite": {"id": suite},
                "app": {"slug": app}, "completed_at": "2025-01-01T00:00:00Z"}

    class Fake(GitHubAPI):
        def _get(self, path, params=None, body=None):
            if path.endswith("/actions/runs"):
                return 200, {"workflow_runs": [
                    {"event": "push", "check_suite_id": 10}, {"event": "issues", "check_suite_id": 20},
                    {"event": "schedule", "check_suite_id": 30}]}, {}
            if path.endswith("/check-runs"):
                return 200, {"check_runs": [
                    run(1, "Build&Test: node-22", 10, "success"),
                    run(2, "evaluate-issue", 20, "failure"),          # an issue workflow failed
                    run(3, "nightly-audit", 30, "failure"),           # a scheduled one failed
                    run(4, "circleci", 99, "failure", app="circleci"),  # other app: no workflow event
                ]}, {}
            return 200, {"statuses": []}, {}

    api = Fake("o", "r", config.Settings())
    every = api.check_conclusion("abc")
    push = api.check_conclusion("abc", events=("push",))
    assert every["required"] == "failure" and {c["name"] for c in every["checks"]} >= {"evaluate-issue", "nightly-audit"}
    assert {c["name"] for c in push["checks"]} == {"build&test: node-22", "circleci"}
    assert push["required"] == "failure"                              # the circleci failure still counts


def test_commit_with_only_automation_runs_has_no_verdict():
    class Fake(GitHubAPI):
        def _get(self, path, params=None, body=None):
            if path.endswith("/actions/runs"):
                return 200, {"workflow_runs": [{"event": "issues", "check_suite_id": 20}]}, {}
            if path.endswith("/check-runs"):
                return 200, {"check_runs": [{"id": 1, "name": "evaluate-issue", "conclusion": "failure",
                                              "check_suite": {"id": 20}, "app": {"slug": "github-actions"}}]}, {}
            return 200, {"statuses": []}, {}

    c = Fake("o", "r", config.Settings()).check_conclusion("abc", events=("push",))
    assert c == {"required": None, "optional": None, "checks": []}     # unobserved, not a pass and not a failure


def test_unreadable_workflow_runs_fall_back_to_counting_everything():
    class Fake(GitHubAPI):
        def _get(self, path, params=None, body=None):
            if path.endswith("/actions/runs"):
                return 403, None, {}
            if path.endswith("/check-runs"):
                return 200, {"check_runs": [{"id": 1, "name": "build", "conclusion": "failure",
                                              "check_suite": {"id": 5}, "app": {"slug": "github-actions"}}]}, {}
            return 200, {"statuses": []}, {}

    assert Fake("o", "r", config.Settings()).check_conclusion("abc", events=("push",))["required"] == "failure"


# ---- maintainers from history ------------------------------------------------------

def _pr(author, events):
    return {"user": {"login": author, "type": "User"}, "_events": events}


def _closed(by, day, kind="closed", bot=False):
    return {"event": kind, "actor": {"login": by, "type": "Bot" if bot else "User"},
            "created_at": iso(T0 + timedelta(days=day))}


def test_evidence_is_merging_or_closing_others_prs():
    ev = maintainers.Evidence.from_prs([
        _pr("alice", [_closed("bob", 10, "merged")]),     # bob merged alice's PR
        _pr("carol", [_closed("carol", 5)]),              # closed her own PR: no evidence
        _pr("dave", [_closed("stale[bot]", 3, bot=True)]),  # bots don't count
        _pr("erin", [_closed("bob", 2)]),                 # bob's earliest
    ])
    assert set(ev.first) == {"bob"}
    assert ev.confirmed("bob", T0 + timedelta(days=3)) and ev.confirmed("Bob", T0 + timedelta(days=30))
    assert not ev.confirmed("bob", T0 + timedelta(days=2))   # not before the moment itself
    assert not ev.confirmed("carol", T0 + timedelta(days=30))


def test_evidence_round_trips(tmp_path):
    ev = maintainers.Evidence.from_prs([_pr("alice", [_closed("bob", 10, "merged")])])
    ev.save(str(tmp_path))
    assert maintainers.Evidence.load(str(tmp_path)).first == ev.first
    assert maintainers.Evidence.load(str(tmp_path / "missing")).first == {}


def test_confirmed_maintainer_is_not_an_outside_contributor():
    st = config.Settings()
    st.llm_sample_pct = 0
    pr = {"number": 1, "author_association": "CONTRIBUTOR", "user": {"login": "bob", "type": "User"}}
    assert mine_repo._llm_in_scope(pr, {"repo": "o/r", "author_confirmed_maintainer": False}, st)
    assert not mine_repo._llm_in_scope(pr, {"repo": "o/r", "author_confirmed_maintainer": True}, st)


def test_snapshot_roles_use_the_evidence():
    ev = maintainers.Evidence.from_prs([_pr("x", [_closed("bob", 10, "merged")])])
    record = {"pr": {"user": {"login": "alice"}}, "events": [
        {"event": "commented", "actor": {"login": "bob"}, "author_association": "CONTRIBUTOR",
         "created_at": iso(T0 + timedelta(days=5))},
        {"event": "commented", "actor": {"login": "bob"}, "author_association": "CONTRIBUTOR",
         "created_at": iso(T0 + timedelta(days=15))}]}
    roles = [r for _w, _k, r, _e in snapshots.Timeline(record, ev).items]
    assert roles == ["other", "maintainer"]                 # only after he first merged
    assert [r for _w, _k, r, _e in snapshots.Timeline(record).items] == ["other", "other"]


def test_get_retries_a_cut_off_response(monkeypatch):
    import http.client
    import io
    import github_api

    calls = []

    class Resp:
        status = 200
        headers = {}

        def __init__(self, cut):
            self.cut = cut

        def read(self):
            if self.cut:
                raise http.client.IncompleteRead(b"x", 5)
            return b'{"ok": true}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_open(req, timeout=None):
        calls.append(1)
        return Resp(cut=len(calls) == 1)

    monkeypatch.setattr(github_api.urllib.request, "urlopen", fake_open)
    monkeypatch.setattr(github_api.time, "sleep", lambda s: None)
    status, data, _ = GitHubAPI("o", "r", config.Settings())._get("/x")
    assert (status, data) == (200, {"ok": True}) and len(calls) == 2


def test_preview_publish_jobs_are_not_build_checks():
    from github_api import _is_optional_check
    assert _is_optional_check("preview") and _is_optional_check("preview release")
    assert not _is_optional_check("build&test: node-22, ubuntu-latest")
