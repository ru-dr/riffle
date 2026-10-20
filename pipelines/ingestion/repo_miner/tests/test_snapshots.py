"""PR snapshots: head commits over time, capture, assembly, leakage, contracts.

A small real repo: base commit B on main; the PR branch has c1 (before open),
c2 (the fix after a changes request), then a force-push replacing c2 with c2',
then c3. The timeline has a maintainer comment and changes request, the
author's fix push, an approval, a bot comment, and the merge.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

MINER = pathlib.Path(__file__).resolve().parent.parent
CONTRACTS = MINER.parents[2] / "contracts"
sys.path.insert(0, str(MINER))

import config  # noqa: E402
import snapshot_capture as cap  # noqa: E402
import snapshots as S  # noqa: E402
from features import merge_signals  # noqa: E402

UTC = timezone.utc


def T(s):
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


def _git(repo, *args, date=None):
    env = dict(os.environ, GIT_AUTHOR_NAME="Alice", GIT_AUTHOR_EMAIL="alice@example.com",
               GIT_COMMITTER_NAME="Alice", GIT_COMMITTER_EMAIL="alice@example.com")
    if date:
        env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    return subprocess.run(["git", *args], cwd=repo, env=env, check=True,
                          capture_output=True, text=True).stdout.strip()


def _commit(repo, text, date, msg):
    (repo / "app.py").write_text(text)
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", msg, date=date)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    repo = tmp_path_factory.mktemp("snap")
    _git(repo, "init", "-q", "-b", "main")
    B = _commit(repo, "def f():\n    return 1\n", "2025-01-01T00:00:00Z", "base")
    _git(repo, "checkout", "-qb", "pr")
    c1 = _commit(repo, "def f():\n    return 1\n\ndef g():\n    return 2\n", "2025-02-01T00:30:00Z", "add g")
    c2 = _commit(repo, "def f():\n    return 1\n\ndef g():\n    return 3\n", "2025-02-03T00:00:00Z", "fix g")
    _git(repo, "tag", "old-c2")                 # keeps the replaced head in the clone
    _git(repo, "reset", "-q", "--hard", c1)
    c2b = _commit(repo, "def f():\n    return 1\n\ndef g():\n    try:\n        return 3\n    except Exception:\n        pass\n",
                  "2025-02-04T23:59:00Z", "fix g (rewritten)")
    c3 = _commit(repo, "def f():\n    return 1\n\ndef g():\n    try:\n        return 3\n    except Exception:\n        pass\n\ndef h():\n    return 4\n",
                 "2025-02-06T00:00:00Z", "add h")
    sha = {"B": B, "c1": c1, "c2": c2, "c2b": c2b, "c3": c3}
    alice = {"login": "alice", "type": "User"}
    bob = {"login": "bob", "type": "User"}
    bot = {"login": "github-actions[bot]", "type": "Bot"}
    events = [
        {"event": "committed", "sha": c1, "committer": {"date": "2025-02-01T00:30:00Z"}, "parents": [B]},
        {"event": "commented", "actor": bob, "author_association": "MEMBER", "created_at": "2025-02-02T00:00:00Z"},
        {"event": "reviewed", "user": bob, "author_association": "MEMBER", "state": "changes_requested",
         "submitted_at": "2025-02-02T00:10:00Z"},
        {"event": "committed", "sha": c2, "committer": {"date": "2025-02-03T00:00:00Z"}, "parents": [c1]},
        {"event": "head_ref_force_pushed", "actor": alice, "created_at": "2025-02-05T00:00:00Z"},
        {"event": "committed", "sha": c2b, "committer": {"date": "2025-02-04T23:59:00Z"}, "parents": [c1]},
        {"event": "committed", "sha": c3, "committer": {"date": "2025-02-06T00:00:00Z"}, "parents": [c2b]},
        {"event": "reviewed", "user": bob, "author_association": "MEMBER", "state": "approved",
         "submitted_at": "2025-02-07T00:00:00Z"},
        {"event": "commented", "actor": bot, "author_association": "NONE", "created_at": "2025-02-08T00:00:00Z"},
        {"event": "merged", "actor": bob, "created_at": "2025-02-10T00:00:00Z"},
        {"event": "closed", "actor": bob, "created_at": "2025-02-10T00:00:00Z"},
    ]
    pr = {"number": 7, "title": "Add g and h", "created_at": "2025-02-01T01:00:00Z",
          "merged_at": "2025-02-10T00:00:00Z", "closed_at": "2025-02-10T00:00:00Z", "draft": False,
          "user": alice, "author_association": "CONTRIBUTOR",
          "base": {"ref": "main", "sha": B}, "head": {"ref": "pr", "sha": c3},
          "_pushes": [("2025-02-05T00:00:00Z", c2)]}
    st = config.Settings()
    st.enable_semgrep = False                 # slow, and not what these tests are about
    schema = json.loads((CONTRACTS / "mined_dataset.schema.json").read_text())
    at_open = {c: None for c in schema["properties"]}
    at_open.update(repo="o/syn", pr_number=7, base_sha=B, head_sha=c1, mined_at="2025-03-01T00:00:00Z",
                   open_snapshot="recovered", author_is_bot=False, label_merged=True, merge_via="github",
                   author_association="CONTRIBUTOR", created_at=pr["created_at"], closed_at=pr["closed_at"],
                   merged_at=pr["merged_at"], miner_version="4.0.0", miner_commit="test")
    at_open.update(cap.code_features(str(repo), B, c1, st))
    record = cap.capture_pr(str(repo), pr, at_open, events, st)
    record = json.loads(json.dumps(record, default=str))      # as stored on disk
    others = [
        {"number": 1, "opened": "2024-12-01T00:00:00Z", "closed": "2025-01-10T00:00:00Z", "merged": True,
         "outsider": True, "agent": None, "first_response": "2024-12-02T00:00:00Z", "tokens": ["g"]},
        {"number": 2, "opened": "2025-01-20T00:00:00Z", "closed": "2025-02-04T00:00:00Z", "merged": False,
         "outsider": True, "agent": None, "first_response": None, "tokens": ["h"]},
        {"number": 3, "opened": "2025-01-25T00:00:00Z", "closed": "2025-02-09T00:00:00Z", "merged": True,
         "outsider": True, "agent": None, "first_response": "2025-01-26T00:00:00Z", "tokens": ["g", "h"]},
    ]
    me = {"number": 7, "opened": pr["created_at"], "closed": pr["closed_at"], "merged": True,
          "outsider": True, "agent": None, "first_response": "2025-02-02T00:00:00Z", "tokens": ["g", "h"]}
    st.merge_signal_min_prs = 1
    return dict(repo=str(repo), sha=sha, pr=pr, events=events, record=record, at_open=at_open,
                sidecars=others + [me], st=st)


# ---- heads over time ------------------------------------------------------------

def test_head_at_follows_pushes_and_force_pushes(world):
    s = world["sha"]
    hl = cap.HeadLine(world["repo"], s["c3"], s["B"], world["pr"]["_pushes"])
    assert hl.at(T("2025-02-01T01:05:00")) == s["c1"]
    assert hl.at(T("2025-02-04T12:00:00")) == s["c2"]          # before the force-push
    assert hl.at(T("2025-02-05T01:00:00")) == s["c2b"]         # after it
    assert hl.at(T("2025-02-08T00:00:00")) == s["c3"]
    line = [sha for _w, sha in hl.changes(T("2025-02-01T01:05:00"), T("2025-02-10T00:00:00"))]
    assert line == [s["c1"], s["c2"], s["c2b"], s["c3"]]


def test_pick_heads_keeps_first_and_last():
    line = [(T(f"2025-02-0{i}T00:00:00"), f"s{i}") for i in range(1, 9)]
    assert [s for _w, s in cap.pick_heads(line, 2)] == ["s1", "s8"]
    assert len(cap.pick_heads(line, 5)) == 5
    assert [s for _w, s in cap.pick_heads(line[:3], 5)] == ["s1", "s2", "s3"]


def test_capture_record(world):
    rec, s = world["record"], world["sha"]
    heads = {h["sha"]: h for h in rec["heads"]}
    assert set(heads) == {s["c1"], s["c2"], s["c2b"], s["c3"]}
    assert heads[s["c1"]].get("at_open") and "features" not in heads[s["c1"]]
    for k in (s["c2"], s["c2b"], s["c3"]):
        assert heads[k]["base_sha"] == s["B"] and heads[k]["features"]["LA"] > 0
    assert rec["events"][1] == {"event": "commented", "created_at": "2025-02-02T00:00:00Z",
                                "author_association": "MEMBER", "actor": {"login": "bob", "type": "User"}}


def test_capture_cap(world, monkeypatch):
    st = config.Settings()
    st.enable_semgrep = False
    st.snapshot_max_heads = 2
    rec = cap.capture_pr(world["repo"], world["pr"], world["at_open"], world["events"], st)
    assert [h["sha"] for h in rec["heads"]] == [world["sha"]["c1"], world["sha"]["c3"]]
    assert len(rec["head_line"]) == 4                         # the full line is kept


# ---- assembly -------------------------------------------------------------------------

def _build(world, record=None, sidecars=None):
    return S.build(record or world["record"], world["at_open"],
                   merge_signals.History(sidecars or world["sidecars"]), world["st"])


def test_triggers_state_and_cleanup(world):
    rows = _build(world)
    trig = [(r["trigger"], r["snapshot_at"]) for r in rows]
    assert trig[0] == ("open", "2025-02-01T01:05:00Z")
    # comment + changes request 10 min apart: one snapshot, at the later one
    assert ("first_maintainer_reply", "2025-02-02T00:10:00Z") in trig
    assert ("author_fix_push", "2025-02-03T00:00:00Z") in trig
    assert ("approval", "2025-02-07T00:00:00Z") in trig
    assert len(rows) <= S.MAX_PER_PR and [r["snapshot_index"] for r in rows] == list(range(len(rows)))
    assert all(r["snapshot_at"] < "2025-02-10T00:00:00Z" for r in rows)   # nothing at/after close
    by = {r["trigger"]: r for r in rows}
    assert by["open"]["maintainer_replied"] is False and by["open"]["review_rounds"] == 0
    fix = by["author_fix_push"]
    assert fix["changes_requested_count"] == 1 and fix["author_pushed_after_last_review"] is True
    assert fix["first_maintainer_reply_h"] == pytest.approx(22.92, abs=0.01)   # from open + 5 min grace
    appr = by["approval"]
    assert appr["approvals_count"] == 1 and appr["review_rounds"] == 2
    assert appr["author_pushed_after_last_review"] is False
    assert appr["force_pushes_since_start"] == 1 and appr["maintainer_comment_count"] == 1
    assert appr["other_comment_count"] == 0                   # the bot comment doesn't count


def test_code_features_follow_the_head(world):
    s = world["sha"]
    by = {r["trigger"]: r for r in _build(world)}
    assert by["open"]["snapshot_head_sha"] == s["c1"] and by["open"]["head_found"] == "open"
    assert by["open"]["LA"] == world["at_open"]["LA"]
    appr = by["approval"]
    assert appr["snapshot_head_sha"] == s["c3"] and appr["head_found"] == "exact"
    assert appr["LA"] > world["at_open"]["LA"]
    assert appr["diff_size_change_since_prev"] is not None


def test_merge_signals_are_as_of_the_snapshot(world):
    by = {r["trigger"]: r for r in _build(world)}
    # PR 2 closed unmerged on 02-04: counted from then on, not at open
    assert by["open"]["repo_outside_closed_count"] == 1
    assert by["approval"]["repo_outside_closed_count"] == 2
    assert by["approval"]["repo_outside_merge_rate"] == 0.5


def _truncate(world, t: datetime):
    """What was knowable at t: later events, head changes, closes and first
    responses removed."""
    rec = json.loads(json.dumps(world["record"]))
    rec["events"] = [e for e in rec["events"]
                     if (cap._ts(e.get("submitted_at") or e.get("created_at")
                                 or (e.get("committer") or {}).get("date")) or t) <= t]
    rec["head_line"] = [[w, sha] for w, sha in rec["head_line"] if cap._ts(w) <= t]
    rec["pushes"] = [p for p in rec["pushes"] if cap._ts(p[0]) <= t]
    rec["pr"]["merged_at"] = rec["pr"]["closed_at"] = None
    side = []
    for sc in world["sidecars"]:
        sc = dict(sc)
        if cap._ts(sc["closed"]) and cap._ts(sc["closed"]) >= t:
            sc["closed"], sc["merged"] = None, None
        if sc.get("first_response") and cap._ts(sc["first_response"]) >= t:
            sc["first_response"] = None
        side.append(sc)
    return rec, side


def test_no_leakage(world):
    """Deleting everything after a snapshot's moment changes none of its inputs."""
    st = world["st"]
    full = S.Context(world["record"], world["at_open"], merge_signals.History(world["sidecars"]), st)
    for r in _build(world):
        t = cap._ts(r["snapshot_at"])
        rec, side = _truncate(world, t)
        cut = S.Context(rec, world["at_open"], merge_signals.History(side), st)
        a = full.row_at(t, r["trigger"], r["trigger_role"])
        b = cut.row_at(t, r["trigger"], r["trigger_role"])
        diff = {k for k in a if k not in S.NOT_INPUTS and a[k] != b[k]}
        assert diff == set(), (r["trigger"], {k: (a[k], b[k]) for k in diff})


def test_draft_clock_starts_at_ready_for_review(world):
    rec = json.loads(json.dumps(world["record"]))
    rec["events"].insert(0, {"event": "ready_for_review", "actor": {"login": "alice", "type": "User"},
                             "created_at": "2025-02-01T20:00:00Z"})
    rows = _build(world, record=rec)
    assert rows[0]["trigger"] == "open" and rows[0]["snapshot_at"] == "2025-02-01T20:00:00Z"
    rec["pr"]["draft"] = True
    rec["events"] = rec["events"][1:]
    assert _build(world, record=rec) == []                    # never left draft


def test_reopen_restarts_and_closed_period_has_no_snapshots(world):
    rec = json.loads(json.dumps(world["record"]))
    rec["events"] += [{"event": "closed", "actor": {"login": "bob", "type": "User"}, "created_at": "2025-02-03T12:00:00Z"},
                      {"event": "reopened", "actor": {"login": "bob", "type": "User"}, "created_at": "2025-02-05T12:00:00Z"}]
    rows = _build(world, record=rec)
    assert not any("2025-02-03T12:00:00Z" <= r["snapshot_at"] < "2025-02-05T12:00:00Z" for r in rows)
    assert any(r["trigger"] == "open" and r["snapshot_at"] == "2025-02-05T12:00:00Z" for r in rows)
    assert max(r["reopen_count"] for r in rows) == 1


# ---- contracts --------------------------------------------------------------------------

def _schema(name):
    return json.loads((CONTRACTS / name).read_text())


def test_snapshot_rows_match_their_contract(world):
    jsonschema = pytest.importorskip("jsonschema")
    schema = _schema("mined_snapshot.schema.json")
    v = jsonschema.Draft202012Validator(schema)
    for r in _build(world):
        assert set(r) == set(schema["properties"])
        assert [e.message for e in v.iter_errors(json.loads(json.dumps(r, default=str)))] == []


def test_commit_rows_match_their_contract(world, tmp_path):
    jsonschema = pytest.importorskip("jsonschema")
    d = tmp_path / "o_syn"
    d.mkdir()
    (d / cap.CAPTURE_FILE).write_text(json.dumps(world["record"]) + "\n")
    (d / cap.INDEX_FILE).write_text("".join(json.dumps(s) + "\n" for s in world["sidecars"]))
    snaps, commits = S.assemble([world["at_open"]], str(tmp_path), world["st"])
    assert snaps and len(commits) == 4
    schema = _schema("mined_commit_features.schema.json")
    v = jsonschema.Draft202012Validator(schema)
    for r in commits:
        assert set(r) == set(schema["properties"])
        assert [e.message for e in v.iter_errors(r)] == []


def test_snapshot_contract_keeps_every_dataset_column():
    d, s = _schema("mined_dataset.schema.json"), _schema("mined_snapshot.schema.json")
    assert all(s["properties"][k] == v for k, v in d["properties"].items())
    assert set(s["properties"]) - set(d["properties"]) == (set(S.STATE_COLUMNS) | set(S.IDENTITY_COLUMNS)
                                                           | set(S.TRAINING_COLUMNS))


def test_weights_add_up_to_one_per_pr_and_buckets(world):
    rows = _build(world)
    assert abs(sum(r["snapshot_weight"] for r in rows) - 1) < 1e-4
    assert rows[0]["moment_bucket"] == "open"
    assert all(r["moment_bucket"] in S.MOMENT_BUCKETS for r in rows)
    assert S.moment_bucket("clock", 30) == "1-3d" and S.moment_bucket("idle", 800) == ">30d"
