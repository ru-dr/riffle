"""snapshots.py, snapshot_capture.py, maintainers.py and merge_signals.History: the v6.13.3 audit fixes
(causal since_prev deltas, dismissed reviews, grace-window closes, empty tables, atomic writes)."""
from __future__ import annotations

import json
import os
import random
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import maintainers  # noqa: E402
import snapshot_capture as cap  # noqa: E402
import snapshots as S  # noqa: E402
from features import merge_signals  # noqa: E402

UTC = timezone.utc
T0 = datetime(2025, 3, 1, tzinfo=UTC)
ALICE = {"login": "alice", "type": "User"}
BOB = {"login": "bob", "type": "User"}


def at(days=0.0, hours=0.0, minutes=0.0):
    return (T0 + timedelta(days=days, hours=hours, minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def review(days, state, rid=None, user=BOB):
    ev = {"event": "reviewed", "user": user, "author_association": "MEMBER", "state": state,
          "submitted_at": at(days)}
    if rid is not None:
        ev["id"] = rid
    return ev


def commit(days):
    return {"event": "committed", "sha": f"c{days}", "committer": {"date": at(days)}, "parents": 1}


def record(events, closed_days=40, draft=False, **extra):
    pr = {"number": 5, "created_at": at(0), "draft": draft, "user": ALICE, "author_association": "CONTRIBUTOR",
          "merged_at": at(closed_days) if closed_days else None,
          "closed_at": at(closed_days) if closed_days else None}
    return {"repo": "o/r", "pr_number": 5, "pr": pr, "events": events, "pushes": [], "head_line": [],
            "heads": [], "open_head": None, **extra}


def st():
    s = config.Settings()
    s.merge_signal_min_prs = 1
    return s


def sidecar(number, opened, closed, merged=True, tokens=("a",), outsider=True):
    return {"number": number, "opened": opened, "closed": closed, "merged": merged, "outsider": outsider,
            "agent": None, "first_response": None, "tokens": list(tokens)}


def build(rec, sidecars=()):
    return S.build(rec, {"repo": "o/r", "pr_number": 5}, merge_signals.History(list(sidecars)), st())


# ---- H6 -----------------------------------------------------------------------------------

def _busy():
    return [{"event": "commented", "actor": BOB, "author_association": "MEMBER", "created_at": at(0.5)},
            review(2, "changes_requested"), commit(2.5), review(4, "changes_requested"),
            review(6, "changes_requested"), review(8, "approved")]


def test_since_prev_deltas_do_not_depend_on_the_future():
    """The cap drops the clock rows between the reviews. A kept row's delta is taken against the previous
    candidate, so cutting the timeline after it changes nothing about it (the old deltas, taken against
    the previous kept row, did: a shorter timeline keeps the early clocks)."""
    full = build(record(_busy()))
    assert len(full) == S.MAX_PER_PR
    assert "clock" not in {r["trigger"] for r in full}                    # the cap really dropped middle rows
    shared = 0
    for r in full:
        t = cap._ts(r["snapshot_at"])
        cut = [e for e in _busy() if (cap._ts(e.get("submitted_at") or e.get("created_at")
                                             or e["committer"]["date"])) <= t]
        rec = record(cut, closed_days=0)
        for c in build(rec):
            if c["snapshot_id"] == r["snapshot_id"]:
                shared += 1
                for k in ("new_commits_since_prev", "new_comments_since_prev", "diff_size_change_since_prev"):
                    assert c[k] == r[k], (r["trigger"], k)
    assert shared >= 4
    d = {r["trigger"]: r for r in full}
    assert d["open"]["new_commits_since_prev"] is None


# ---- M27 / L16 ----------------------------------------------------------------------------

def _dismissed(with_event=True):
    ev = [review(1, "dismissed", rid=11)]
    if with_event:
        ev.append({"event": "review_dismissed", "actor": BOB, "created_at": at(3),
                   "dismissed_review": {"review_id": 11, "state": "changes_requested"}})
    return record(ev)


def test_a_dismissed_review_counts_with_its_original_state_until_dismissed():
    ctx = S.Context(_dismissed(), {}, merge_signals.History([]), st())
    before, after = ctx.state_at(T0 + timedelta(days=2)), ctx.state_at(T0 + timedelta(days=4))
    assert before["changes_requested_count"] == 1 and before["review_rounds"] == 1
    assert after["changes_requested_count"] == 0 and after["review_rounds"] == 1


def test_a_dismissed_review_in_an_old_capture_is_unknown_not_zero():
    ctx = S.Context(_dismissed(with_event=False), {}, merge_signals.History([]), st())
    s = ctx.state_at(T0 + timedelta(days=2))
    assert s["changes_requested_count"] is None and s["approvals_count"] is None and s["review_rounds"] == 1
    assert ctx.state_at(T0 + timedelta(hours=1))["changes_requested_count"] == 0     # before the review


def test_capture_keeps_review_ids_and_the_dismissed_state():
    ev = cap.slim_event({"event": "reviewed", "id": 11, "state": "dismissed", "user": BOB, "node_id": "x"})
    assert ev["id"] == 11
    d = cap.slim_event({"event": "review_dismissed", "created_at": at(3), "actor": BOB,
                        "dismissed_review": {"review_id": 11, "state": "approved", "dismissal_message": "m"}})
    assert d["dismissed_review"] == {"review_id": 11, "state": "approved"}


def test_first_maintainer_reply_is_never_negative():
    ev = [{"event": "commented", "actor": BOB, "author_association": "MEMBER", "created_at": at(minutes=1)}]
    ctx = S.Context(record(ev), {}, merge_signals.History([]), st())      # reply inside the 5-minute grace
    assert ctx.state_at(T0 + timedelta(days=1))["first_maintainer_reply_h"] == 0.0


# ---- M28 ---------------------------------------------------------------------------------

def test_no_merge_outcome_capture_keeps_merge_rates_null():
    side = [sidecar(1, at(-20), at(-10), merged=True), sidecar(5, at(0), at(40))]
    on = build(record(_busy()), side)[0]
    off = build(record(_busy(), merge_outcome=False), side)[0]
    assert on["repo_outside_merge_rate"] == 1.0 and off["repo_outside_merge_rate"] is None
    assert off["repo_outside_closed_count"] == 1                 # counts are not rates


def test_capture_pr_records_the_merge_outcome_setting(monkeypatch):
    s = config.Settings()
    s.enable_merge_outcome = False
    monkeypatch.setattr(cap.gitio, "rev_parse_safe", lambda *a: "x")
    monkeypatch.setattr(cap.HeadLine, "changes", lambda self, a, b: [])
    pr = {"number": 5, "created_at": at(0), "closed_at": at(2), "head": {"sha": "h"}, "base": {"sha": "b"}}
    assert cap.capture_pr("/nowhere", pr, {"repo": "o/r"}, [], s)["merge_outcome"] is False


# ---- L14 / L15 / L17 ---------------------------------------------------------------------

def test_a_close_inside_the_grace_window_leaves_no_period():
    ev = [{"event": "closed", "actor": ALICE, "created_at": at(minutes=2)},
          {"event": "reopened", "actor": ALICE, "created_at": at(2)}]
    rec = record(ev)
    tl = S.Timeline(rec)
    segs = S.open_segments(rec, tl, timedelta(minutes=5))
    assert segs[0][0] == T0 + timedelta(days=2)


def test_a_pr_that_ended_as_a_draft_still_gets_snapshots():
    ev = [{"event": "convert_to_draft", "actor": ALICE, "created_at": at(3)}]
    rec = record(ev, draft=True)
    assert S.open_segments(rec, S.Timeline(rec), timedelta(minutes=5))
    assert S.open_segments(record([], draft=True), S.Timeline(record([], draft=True)), timedelta(minutes=5)) == []


def test_an_unreadable_head_is_not_reported_as_exact():
    rec = record([], head_line=[[at(1), "bad"], [at(2), "good"]], pushes=[],
                 heads=[{"sha": "bad", "error": "boom", "first_seen": at(1)},
                        {"sha": "good", "first_seen": at(2), "features": {"LA": 3}, "base_sha": "b"}],
                 open_head="o")
    ctx = S.Context(rec, {"LA": 1}, merge_signals.History([]), st())
    assert ctx.head_at(T0 + timedelta(days=1, hours=1)) == ("o", "capped")
    assert ctx.head_at(T0 + timedelta(days=3)) == ("good", "exact")
    rec["heads"][0].pop("error")
    rec["heads"][0]["at_open"] = False                              # no features either
    assert S.Context(rec, {}, merge_signals.History([]), st()).head_at(T0 + timedelta(days=1, hours=1))[1] == "capped"


# ---- M29 / L87 / L88 ---------------------------------------------------------------------

def test_a_duplicate_sidecar_does_not_match_the_pr_to_itself():
    me, other = sidecar(5, at(0), at(40), tokens=("x", "y")), sidecar(6, at(1), at(30), tokens=("p", "q"))
    h = merge_signals.History([me, dict(me), other])
    p = h.by_number[5]
    assert h.at(p, T0 + timedelta(days=2), st(), True)["max_sim_open_prs"] == 0.0


def test_history_at_matches_a_brute_force_scan():
    rnd = random.Random(3)
    sc = []
    for n in range(80):
        o = rnd.uniform(-60, 60)
        closed = None if rnd.random() < 0.15 else at(o + rnd.uniform(0.1, 90))
        sc.append(sidecar(n, at(o), closed, tokens=rnd.sample("abcdefgh", 3)))
    h = merge_signals.History(sc)
    for p in h.prs[:25]:
        for d in (-10, 5, 30, 70):
            t = T0 + timedelta(days=d)
            want = max((merge_signals._jaccard(p["tokens"], q["tokens"]) for q in h.prs
                        if q is not p and q["opened"] < t and q["closed"] > t), default=0.0)
            assert h.at(p, t, st(), True)["max_sim_open_prs"] == round(want, 4)


def test_assembly_parses_each_capture_once_and_takes_the_last_duplicate(tmp_path, monkeypatch):
    d = tmp_path / "o_r"
    d.mkdir()
    rec = record(_busy())
    (d / cap.CAPTURE_FILE).write_text(json.dumps(rec) + "\n" + json.dumps(rec) + "\n{torn")
    me = sidecar(5, at(0), at(40), tokens=("x",))
    (d / cap.INDEX_FILE).write_text(json.dumps(me) + "\n" + json.dumps(me) + "\n")
    calls = []
    real = cap.read_jsonl
    monkeypatch.setattr(cap, "read_jsonl", lambda p: (calls.append(os.path.basename(p)), real(p))[1])
    snaps, _ = S.assemble([{"repo": "o/r", "pr_number": 5}], str(tmp_path), st())
    assert calls.count(cap.CAPTURE_FILE) == 1 and snaps


# ---- M36 / L18 ---------------------------------------------------------------------------

def test_empty_tables_keep_the_contract_columns_and_are_written_together(tmp_path):
    pd = pytest.importorskip("pandas")
    ds = tmp_path / "dataset.parquet"
    pd.DataFrame([{"repo": "o/r", "pr_number": 5}]).to_parquet(ds)
    out, cout = str(tmp_path / "s.parquet"), str(tmp_path / "c.parquet")
    r = S.assemble_files(str(ds), str(tmp_path / "none"), out, cout)
    assert r["snapshots"] == 0
    s, c = pd.read_parquet(out), pd.read_parquet(cout)
    assert s.empty and "snapshot_id" in s.columns and "near_decided" in s.columns
    assert c.empty and "head_sha" in c.columns
    assert not [f for f in os.listdir(tmp_path) if f.endswith(".tmp")]


def test_empty_jsonl_tables_are_empty_files(tmp_path):
    ds = tmp_path / "dataset.jsonl"
    ds.write_text(json.dumps({"repo": "o/r", "pr_number": 5}) + "\n")
    out, cout = str(tmp_path / "s.jsonl"), str(tmp_path / "c.jsonl")
    S.assemble_files(str(ds), str(tmp_path / "none"), out, cout)
    assert os.path.getsize(out) == 0 and os.path.getsize(cout) == 0


# ---- L33 ---------------------------------------------------------------------------------

def test_maintainer_evidence_saves_atomically_and_loads_tolerantly(tmp_path):
    ev = maintainers.Evidence({"bob": T0})
    ev.save(str(tmp_path))
    assert os.listdir(tmp_path) == [maintainers.FILE]
    assert maintainers.Evidence.load(str(tmp_path)).first == {"bob": T0}
    path = tmp_path / maintainers.FILE
    path.write_text('{"bob": "2025-03-01T00:00:00Z", "eve": "not a date"}')
    assert set(maintainers.Evidence.load(str(tmp_path)).first) == {"bob"}
    path.write_text('{"bob": "2025-03')                          # torn write
    assert maintainers.Evidence.load(str(tmp_path)).first == {}
