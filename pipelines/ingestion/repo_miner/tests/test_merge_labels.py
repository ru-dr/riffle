"""
Merge-outcome labels (ADR 0009) on synthetic timelines, plus the repo-local
history columns for rows of PRs that never merged.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from features import repo_local  # noqa: E402
from merge_labels import build_merge_labels  # noqa: E402

ST = config.Settings()
AUTHOR = {"login": "alice", "type": "User"}
MAINT = {"login": "bob", "type": "User"}


def _pr(merged: bool, **kw) -> dict:
    pr = {"number": 7, "user": AUTHOR, "created_at": "2026-01-01T00:00:00Z",
          "closed_at": "2026-01-05T00:00:00Z",
          "merged_at": "2026-01-05T00:00:00Z" if merged else None}
    pr.update(kw)
    return pr


def _comment(user, body, at="2026-01-02T00:00:00Z", assoc="MEMBER"):
    return {"event": "commented", "user": user, "body": body, "created_at": at,
            "author_association": assoc}


def _review(user, state, at="2026-01-02T00:00:00Z", assoc="MEMBER"):
    return {"event": "reviewed", "user": user, "state": state, "submitted_at": at,
            "author_association": assoc, "body": ""}


def _commit(at):
    return {"event": "committed", "sha": "a" * 40, "committer": {"date": at}}


def test_merged_unchanged():
    ev = [_commit("2025-12-31T23:00:00Z"), _review(MAINT, "approved")]
    out = build_merge_labels(_pr(True), ev, None, ST)
    assert out["label_merged"] is True
    assert out["label_rework"] is False
    assert out["close_reason"] is None and out["label_abandoned"] is None


def test_merged_after_rework_by_commit_and_review():
    ev = [_review(MAINT, "changes_requested"), _commit("2026-01-03T00:00:00Z")]
    out = build_merge_labels(_pr(True), ev, None, ST)
    assert out["label_rework"] is True
    assert out["post_open_commits"] == 1 and out["changes_requested_reviews"] == 1


def test_commit_within_grace_is_not_rework():
    ev = [_commit("2026-01-01T00:02:00Z")]               # 2 min after open
    assert build_merge_labels(_pr(True), ev, None, ST)["label_rework"] is False


def test_own_or_bot_change_request_is_not_rework():
    bot = {"login": "ci[bot]", "type": "Bot"}
    ev = [_review(AUTHOR, "changes_requested"), _review(bot, "changes_requested")]
    assert build_merge_labels(_pr(True), ev, None, ST)["label_rework"] is False


def test_duplicate():
    ev = [_comment(MAINT, "Duplicate of #3, closing.")]
    out = build_merge_labels(_pr(False), ev, "success", ST)
    assert out["label_merged"] is False
    assert out["label_duplicate"] is True and out["close_reason"] == "duplicate"


def test_marked_as_duplicate_event():
    ev = [{"event": "marked_as_duplicate", "actor": MAINT, "created_at": "2026-01-02T00:00:00Z"}]
    assert build_merge_labels(_pr(False), ev, None, ST)["label_duplicate"] is True


def test_unwanted_needs_a_maintainer():
    out = build_merge_labels(_pr(False), [_comment(MAINT, "This is out of scope for us.")],
                             None, ST)
    assert out["label_unwanted"] is True and out["close_reason"] == "unwanted"
    drive_by = {"login": "carol", "type": "User"}
    out = build_merge_labels(
        _pr(False), [_comment(drive_by, "out of scope imo", assoc="NONE")], None, ST)
    assert out["label_unwanted"] is False


def test_wontfix_label_is_unwanted():
    ev = [{"event": "labeled", "label": {"name": "wontfix"}, "actor": MAINT,
           "created_at": "2026-01-04T00:00:00Z"}]
    assert build_merge_labels(_pr(False), ev, None, ST)["label_unwanted"] is True
    ev[0]["label"]["name"] = "rejected AI"
    ev[0]["created_at"] = "2026-01-05T00:00:10Z"            # labelled just after close
    assert build_merge_labels(_pr(False), ev, None, ST)["label_unwanted"] is True


def test_abandoned_without_human_response():
    ev = [_comment(AUTHOR, "ping?", assoc="CONTRIBUTOR")]
    out = build_merge_labels(_pr(False), ev, None, ST)
    assert out["label_abandoned"] is True and out["close_reason"] == "abandoned"


def test_abandoned_by_stale_bot():
    stale = {"login": "stale[bot]", "type": "Bot"}
    ev = [_comment(MAINT, "Looks interesting"),
          _comment(stale, "This PR is stale because it has no recent activity.", assoc="NONE"),
          {"event": "closed", "actor": stale, "created_at": "2026-01-05T00:00:00Z"}]
    assert build_merge_labels(_pr(False), ev, None, ST)["label_abandoned"] is True


def test_ci_technical_and_objection():
    ev = [_comment(MAINT, "thanks, looking")]
    out = build_merge_labels(_pr(False), ev, "failure", ST)
    assert out["label_ci_technical"] is True and out["close_reason"] == "ci_technical"
    ev = [_review(MAINT, "changes_requested")]
    assert build_merge_labels(_pr(False), ev, "failure", ST)["label_ci_technical"] is False
    assert build_merge_labels(_pr(False), ev, None, ST)["label_ci_technical"] is None


def test_events_after_close_are_ignored():
    ev = [_comment(MAINT, "thanks"), _comment(MAINT, "duplicate of #9", at="2026-02-01T00:00:00Z")]
    out = build_merge_labels(_pr(False), ev, "success", ST)
    assert out["label_duplicate"] is False and out["close_reason"] == "other"


def test_unreadable_timeline_keeps_only_merged():
    out = build_merge_labels(_pr(False), None, "failure", ST)
    assert out["label_merged"] is False
    # it went nowhere, which is known; why is not
    assert out["outcome_3class"] == "goes_nowhere" and out["nowhere_reason"] is None
    assert all(out[k] is None for k in out if k not in ("label_merged", "outcome_3class"))


def test_targets():
    from merge_labels import targets
    assert targets({"label_merged": True, "label_rework": False})["outcome_3class"] == "lands_clean"
    assert targets({"label_merged": True, "label_rework": True})["outcome_3class"] == "lands_after_rework"
    assert targets({"label_merged": True, "label_rework": None})["outcome_3class"] is None
    t = targets({"label_merged": False, "close_reason": "duplicate"})
    assert t == {"outcome_3class": "goes_nowhere", "nowhere_reason": "duplicate"}
    assert targets({"label_merged": False, "close_reason": "other"})["nowhere_reason"] == "unknown"
    assert targets({})["outcome_3class"] is None


def test_repo_local_fills_unmerged_rows_without_using_them_as_history():
    def row(num, opened, merged, files, bad=False):
        return {"pr_number": num, "merged_at": merged, "label_bad": bad,
                "label_revert": bad, "days_to_revert": 1.0 if bad else None,
                repo_local.SIDECAR: {"opened_at": opened, "files": {f: 1 for f in files},
                                     "reviews": None}}
    rows = [row(i, f"2025-0{1 + i // 10}-{1 + i % 10:02d}T00:00:00Z",
                f"2025-0{1 + i // 10}-{1 + i % 10:02d}T01:00:00Z", ["a.py"], bad=i % 2 == 0)
            for i in range(20)]
    unmerged = row(99, "2025-12-01T00:00:00Z", None, ["a.py"])
    late = row(100, "2025-12-02T00:00:00Z", "2025-12-02T01:00:00Z", ["a.py"])
    repo_local.compute_repo(rows + [unmerged, late], [], ST)
    assert unmerged["similar_pr_count"] == late["similar_pr_count"] > 0
    assert unmerged["similar_pr_bad_rate"] == late["similar_pr_bad_rate"]


def test_llm_reason_decides_close_reason_and_unwanted():
    ev = [_comment(MAINT, "We prefer a different approach, closing.")]
    out = build_merge_labels(_pr(False), ev, "success", ST, llm_reason="unwanted")
    assert out["close_reason"] == "unwanted" and out["close_reason_source"] == "llm"
    assert out["label_unwanted"] is True
    # an LLM "other" overrides a phrase-rule unwanted, but not a duplicate event
    ev = [_comment(MAINT, "out of scope"),
          {"event": "marked_as_duplicate", "actor": MAINT, "created_at": "2026-01-02T00:00:00Z"}]
    out = build_merge_labels(_pr(False), ev, None, ST, llm_reason="other")
    assert out["label_unwanted"] is False and out["label_duplicate"] is True
    assert out["close_reason"] == "other"


def test_rules_source_without_llm():
    out = build_merge_labels(_pr(False), [_comment(MAINT, "Duplicate of #3")], None, ST)
    assert out["close_reason_source"] == "rules"


def test_transcript_needs_a_human():
    from merge_labels import close_transcript
    bot = {"login": "ci[bot]", "type": "Bot"}
    assert close_transcript(_pr(False), [_comment(bot, "build failed", assoc="NONE")]) is None
    text = close_transcript(_pr(False, title="Add X"), [_comment(MAINT, "not for us")])
    assert text.startswith("Title: Add X") and "[maintainer] not for us" in text


def test_classifier_off_without_llm():
    from merge_labels import classify_close
    st = config.Settings()
    st.enable_llm = False
    assert classify_close(_pr(False), [_comment(MAINT, "no thanks")], st) is None


def test_stale_open_is_abandoned():
    pr = _pr(False, closed_at=None, _stale_open=True)
    out = build_merge_labels(pr, [_comment(AUTHOR, "ping", assoc="CONTRIBUTOR")], None, ST,
                             llm_reason="unwanted")
    assert out["label_merged"] is False and out["label_abandoned"] is True
    assert out["close_reason"] == "abandoned"


def test_transcript_for_silent_maintainer_close():
    from merge_labels import close_transcript
    ev = [{"event": "closed", "actor": MAINT, "created_at": "2026-01-05T00:00:00Z",
           "author_association": "MEMBER"},
          {"event": "renamed", "actor": MAINT, "created_at": "2026-01-05T00:00:20Z",
           "author_association": "MEMBER", "rename": {"from": "Fix", "to": "AI spam"}}]
    text = close_transcript(_pr(False, title="AI spam"), ev)
    assert text is not None and "'Fix' -> 'AI spam'" in text


def test_concurrency_index_matches_all_pairs(monkeypatch):
    """The file-indexed, parallel _build_concurrency gives the all-pairs answer."""
    import random
    import gitio
    import mine_repo
    rng = random.Random(7)
    prs, lines = [], {}
    for n in range(60):
        day = rng.randint(1, 20)
        prs.append({"number": n, "created_at": f"2026-01-{day:02d}T00:00:00Z",
                    "closed_at": f"2026-01-{min(28, day + rng.randint(0, 6)):02d}T12:00:00Z",
                    "_open": {"base": f"b{n}", "head": f"h{n}"}})
        lines[f"h{n}"] = {f"f{rng.randint(0, 9)}.py": {rng.randint(1, 30) for _ in range(3)}
                          for _ in range(rng.randint(1, 3))}
    monkeypatch.setattr(gitio, "changed_lines_by_file", lambda repo, b, h: lines[h])
    got = mine_repo._build_concurrency("repo", prs, None, workers=4)
    ts = mine_repo._parse_ts
    for p in prs:
        sf = sh = 0
        for q in prs:
            if q is p or not (ts(q["created_at"]) <= ts(p["created_at"]) < ts(q["closed_at"])):
                continue
            lp, lq = lines[p["_open"]["head"]], lines[q["_open"]["head"]]
            shared = set(lp) & set(lq)
            if shared:
                sf += 1
                sh += any(lp[f] & lq[f] for f in shared)
        assert got[p["number"]] == {"concurrent_open_prs_same_files": sf,
                                    "concurrent_open_prs_same_hunks": sh}


def test_rework_ignores_base_branch_merges():
    ev = [{"event": "committed", "sha": "m" * 40, "committer": {"date": "2026-01-03T00:00:00Z"},
           "parents": [{"sha": "a"}, {"sha": "b"}]}]
    assert build_merge_labels(_pr(True), ev, None, ST)["label_rework"] is False


def test_at_open_title_and_milestone():
    from merge_labels import at_open
    from datetime import datetime, timezone
    ev = [{"event": "milestoned", "created_at": "2026-01-03T00:00:00Z"},
          {"event": "renamed", "created_at": "2026-01-04T00:00:00Z",
           "rename": {"from": "Add feature", "to": "AI spam"}}]
    got = at_open(_pr(False, title="AI spam"), ev, datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc))
    assert got["title"] == "Add feature" and got["has_milestone"] is False


def test_partial_timeline_is_unobserved(monkeypatch):
    from github_api import GitHubAPI
    api = GitHubAPI("o", "r", config.Settings())
    pages = iter([(200, [{"event": "commented"}] * 100, {}), (502, None, {})])
    monkeypatch.setattr(api, "_get", lambda *a, **k: next(pages))
    assert api.pr_timeline(1) is None


def test_no_token_force_pushes_unknown():
    from github_api import GitHubAPI
    st = config.Settings()
    st.github_token = None
    api = GitHubAPI("o", "r", st)
    assert api.force_pushes([1, 2]) == {1: None, 2: None}
