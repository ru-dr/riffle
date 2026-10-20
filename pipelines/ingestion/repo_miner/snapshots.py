#!/usr/bin/env python3
"""
snapshots.py — PR snapshots, assembled from stored data (step 2 of 2).

A snapshot is a training row describing a PR at a later moment while it was
open, labelled with how the PR finally ended. The at-open row (the mined
dataset) only shows PRs as they opened; snapshots teach the model what states
like "changes requested, author pushed a fix" or "7 days without a maintainer
reply" mean, so re-scoring a live PR as events arrive gives a better answer.

Reads what mining stored (snapshot_capture.py: <raw-root>/<owner>_<name>/
capture.jsonl and pr_index.jsonl) plus the mined at-open rows, and writes:

  snapshots         one row per kept snapshot: the at-open row's columns, with
                    the code features of the head at that moment, the merge
                    signals recomputed as of that moment, and the state so far
  commit_features   one row per captured head commit

No GitHub calls and no clones, so the schedule below can change without
re-mining.

Moments (only while the PR is open; the clock starts at open, or at
ready-for-review for a PR opened as a draft, and restarts at a reopen):
  events  first maintainer reply (comment or review), each changes-requested
          review (up to 3), first approval, the author's first push after a
          changes request, an outcome label or "marked as duplicate", back to
          draft
  clocks  1, 3, 7, 14, 30 days after the start; 7 and 14 days without human
          activity; 7 days without a maintainer reply after the author's last
          push or comment
Clean-up: events within 30 min make one snapshot at the last of them; a
snapshot whose state matches the previous one is dropped unless it is a
clock; snapshots within 6 h with the same state keep the higher-priority one;
nothing at or after close; at most 6 per PR, by priority. Bots count for
nothing. near_decided marks the last hour before close and anything after
the PR entered a merge queue or got auto-merge: it is for filtering in
training and is never a model input.

Usage:
  python snapshots.py --dataset dataset.parquet --raw-root snapshots_raw \
      --out snapshots.parquet --commit-features-out commit_features.parquet
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import config
import maintainers
import snapshot_capture as cap
from features import merge_signals
from merge_labels import MAINTAINER_ASSOCIATIONS, _event_time, _event_user, _is_bot, _login

CLOCK_DAYS = (1, 3, 7, 14, 30)
IDLE_DAYS = (7, 14)
NO_REPLY_DAYS = 7
BURST = timedelta(minutes=30)
NEIGHBOUR = timedelta(hours=6)
NEAR_CLOSE = timedelta(hours=1)
MAX_CHANGES_REQUESTED = 3
MAX_PER_PR = 6
OUTCOME_LABELS = ("duplicate", "wontfix", "won't fix", "invalid", "needs-info", "needs info",
                  "stale", "blocked", "waiting-on-author", "waiting for author", "not planned")
MERGE_QUEUE_EVENTS = {"added_to_merge_queue", "auto_merge_enabled", "auto_squash_enabled",
                      "auto_rebase_enabled"}

# lower = kept first when a PR has more than MAX_PER_PR
PRIORITY = {"open": 0, "first_maintainer_reply": 1, "changes_requested": 2, "approval": 3,
            "author_fix_push": 4, "outcome_label": 5, "marked_duplicate": 5, "back_to_draft": 6,
            "clock": 7, "idle": 8, "no_maintainer_reply": 8}

STATE_COLUMNS = (
    "time_since_start_h", "time_since_last_human_activity_h", "maintainer_replied",
    "first_maintainer_reply_h", "review_rounds", "changes_requested_count", "approvals_count",
    "commits_since_start", "force_pushes_since_start", "author_pushed_after_last_review",
    "maintainer_comment_count", "author_comment_count", "other_comment_count",
    "has_outcome_label", "marked_duplicate_so_far", "is_draft_now", "reopen_count",
    "new_commits_since_prev", "new_comments_since_prev", "diff_size_change_since_prev",
)
IDENTITY_COLUMNS = ("snapshot_id", "snapshot_at", "snapshot_index", "trigger", "trigger_role",
                    "snapshot_head_sha", "snapshot_base_sha", "head_found", "near_decided")
# for training and reporting: snapshot_weight = 1 / the PR's snapshot count, so
# each PR adds up to 1 and slow PRs with many snapshots don't dominate;
# moment_bucket = the time band of the snapshot, for metrics per moment
TRAINING_COLUMNS = ("snapshot_weight", "moment_bucket")
MOMENT_BUCKETS = ("open", "<1d", "1-3d", "3-7d", "7-30d", ">30d")
# columns that are outcomes, filters or weights, never model inputs
NOT_INPUTS = ("near_decided", "snapshot_weight")


def moment_bucket(trigger: str, hours: float) -> str:
    if trigger == "open":
        return "open"
    for limit, name in ((24, "<1d"), (72, "1-3d"), (168, "3-7d"), (720, "7-30d")):
        if hours < limit:
            return name
    return ">30d"


def _ts(s) -> datetime | None:
    return cap._ts(s)


def _iso(t: datetime) -> str:
    return cap._iso(t)


# ---- reading a PR's timeline --------------------------------------------------

class Timeline:
    """A PR's events with roles (author / maintainer / other / bot)."""

    def __init__(self, record: dict, evidence: maintainers.Evidence | None = None):
        pr = record["pr"]
        self.pr = pr
        self.evidence = evidence or maintainers.Evidence()
        self.author = _login(pr.get("user"))
        self.items = []
        for ev in record.get("events") or []:
            when = _event_time(ev)
            if when is None:
                continue
            self.items.append((when, ev.get("event"), self._role(ev, when), ev))
        self.items.sort(key=lambda x: x[0])

    def _role(self, ev: dict, when: datetime) -> str:
        if ev.get("event") == "committed":
            return "author"                   # commits on the PR branch
        user = _event_user(ev)
        if _is_bot(user):
            return "bot"
        if _login(user) == self.author:
            return "author"
        if str(ev.get("author_association", "")).upper() in MAINTAINER_ASSOCIATIONS:
            return "maintainer"
        if self.evidence.confirmed(_login(user), when):
            return "maintainer"               # had merged or closed others' PRs before
        return "other"

    def until(self, t: datetime):
        return [x for x in self.items if x[0] <= t]


def open_segments(record: dict, tl: Timeline, grace: timedelta) -> list[tuple[datetime, datetime]]:
    """[(start, end)] periods the PR was open and ready for review."""
    pr = record["pr"]
    opened = _ts(pr.get("created_at"))
    if opened is None:
        return []
    end = _ts(pr.get("merged_at") or pr.get("closed_at"))
    if end is None:                           # still open (abandoned): the 60-day window
        end = opened + timedelta(days=60)
    kinds = [(w, k) for w, k, _r, _e in tl.items]
    ready = [w for w, k in kinds if k == "ready_for_review"]
    to_draft = [w for w, k in kinds if k == "convert_to_draft"]
    start = opened + grace
    if ready and (not to_draft or ready[0] < to_draft[0]):
        start = ready[0]                      # opened as a draft
    elif pr.get("draft") and not ready:
        return []                             # never left draft
    segs, cur = [], start
    for w, k in kinds:
        if k == "closed" and cur is not None and w < end and w > cur:
            segs.append((cur, w))
            cur = None
        elif k == "reopened" and cur is None and w < end:
            cur = w
    if cur is not None and end > cur:
        segs.append((cur, end))
    return segs


def _is_outcome_label(name: str, st: config.Settings) -> bool:
    n = (name or "").lower()
    return any(o in n for o in OUTCOME_LABELS) or any(u in n for u in st.unwanted_labels)


def candidates(record: dict, tl: Timeline, segs, st: config.Settings) -> list[tuple[datetime, str, str]]:
    """[(time, trigger, trigger_role)] before clean-up."""
    out = []
    for s, e in segs:
        out.append((s, "open", "author"))
        for d in CLOCK_DAYS:
            t = s + timedelta(days=d)
            if t < e:
                out.append((t, "clock", "clock"))
    seen_reply = seen_approval = seen_fix = seen_label = seen_dup = False
    cr = 0
    last_cr = None
    in_seg = lambda t: any(s <= t < e for s, e in segs)  # noqa: E731
    for when, kind, role, ev in tl.items:
        if not in_seg(when) or role == "bot":
            continue
        state = str(ev.get("state", "")).lower()
        if role == "maintainer" and kind in ("commented", "reviewed") and not seen_reply:
            out.append((when, "first_maintainer_reply", role))
            seen_reply = True
        if kind == "reviewed" and role != "author" and state == "changes_requested":
            last_cr = when
            if cr < MAX_CHANGES_REQUESTED:
                out.append((when, "changes_requested", role))
                cr += 1
        if kind == "reviewed" and role != "author" and state == "approved" and not seen_approval:
            out.append((when, "approval", role))
            seen_approval = True
        if (kind in ("committed", "head_ref_force_pushed") and role == "author"
                and last_cr is not None and when > last_cr and not seen_fix):
            out.append((when, "author_fix_push", role))
            seen_fix = True
        if kind == "labeled" and not seen_label and _is_outcome_label(
                (ev.get("label") or {}).get("name"), st):
            out.append((when, "outcome_label", role))
            seen_label = True
        if kind == "marked_as_duplicate" and not seen_dup:
            out.append((when, "marked_duplicate", role))
            seen_dup = True
        if kind == "convert_to_draft" and role == "author":
            out.append((when, "back_to_draft", role))
    # idle clocks, from human activity only
    human = sorted({w for w, k, r, _e in tl.items if r != "bot" and in_seg(w)})
    for s, e in segs:
        acts = [s] + [w for w in human if s <= w < e]
        for i, a in enumerate(acts):
            nxt = acts[i + 1] if i + 1 < len(acts) else e
            for d in IDLE_DAYS:
                t = a + timedelta(days=d)
                if t < nxt:
                    out.append((t, "idle", "clock"))
        author_acts = [w for w, k, r, _e in tl.items if r == "author" and s <= w < e]
        maint = [w for w, k, r, _e in tl.items if r == "maintainer" and s <= w < e]
        for a in author_acts:
            t = a + timedelta(days=NO_REPLY_DAYS)
            nxt_author = next((w for w in author_acts if w > a), e)
            if t < min(nxt_author, e) and not any(a < m <= t for m in maint):
                out.append((t, "no_maintainer_reply", "clock"))
    return sorted(out, key=lambda x: (x[0], PRIORITY[x[1]]))


def debounce(cands):
    """Event triggers within BURST of each other become one, at the last."""
    out = []
    for c in cands:
        is_event = c[2] != "clock" and c[1] != "open"
        if out and is_event and out[-1][2] != "clock" and out[-1][1] != "open" \
                and c[0] - out[-1][0] <= BURST:
            prev = out.pop()
            best = prev if PRIORITY[prev[1]] <= PRIORITY[c[1]] else c
            out.append((c[0], best[1], best[2]))
        else:
            out.append(c)
    return out


# ---- the state at a moment ------------------------------------------------------

class Context:
    """Everything a snapshot of one PR is computed from. Truncating it to a
    moment must not change a snapshot at or before that moment (the leakage
    test does exactly that)."""

    def __init__(self, record: dict, at_open: dict, history: merge_signals.History,
                 st: config.Settings, evidence: maintainers.Evidence | None = None):
        self.record, self.at_open, self.history, self.st = record, at_open, history, st
        self.tl = Timeline(record, evidence)
        self.grace = timedelta(seconds=max(0, st.rework_grace_s))
        self.segs = open_segments(record, self.tl, self.grace)
        self.head_line = [(_ts(w), s) for w, s in record.get("head_line") or []]
        self.heads = {h["sha"]: h for h in record.get("heads") or []}
        self.hist_pr = next((p for p in history.prs
                             if p["number"] == record.get("pr_number")), None)
        self.close = _ts(record["pr"].get("merged_at") or record["pr"].get("closed_at"))
        self.merge_queue_at = min((w for w, k, _r, _e in self.tl.items if k in MERGE_QUEUE_EVENTS),
                                  default=None)
        self.code_columns = sorted({k for h in self.heads.values()
                                    for k in (h.get("features") or {})})

    def head_at(self, t: datetime) -> tuple[str | None, str]:
        line = [s for w, s in self.head_line if w <= t]
        if not line:
            return self.record.get("open_head"), "open"
        sha = line[-1]
        if sha == self.record.get("open_head"):
            return sha, "open"
        exact = "exact" if self.record.get("pushes") is not None else "approx"
        if sha in self.heads:
            return sha, exact
        captured = [s for s in line if s in self.heads]   # latest captured head before t
        return (captured[-1], "capped") if captured else (self.record.get("open_head"), "capped")

    def code_at(self, sha: str | None) -> tuple[dict, str | None]:
        h = self.heads.get(sha) if sha else None
        if h is None or h.get("at_open") or not h.get("features"):
            return {c: self.at_open.get(c) for c in self.code_columns}, self.at_open.get("base_sha")
        return dict(h["features"]), h.get("base_sha")

    def state_at(self, t: datetime) -> dict:
        start = max((s for s, e in self.segs if s <= t), default=self.segs[0][0])
        items = self.tl.until(t)
        human = [x for x in items if x[2] != "bot"]
        last_human = max([start] + [w for w, *_ in human])
        maint = [x for x in human if x[2] == "maintainer" and x[1] in ("commented", "reviewed")]
        reviews = [x for x in human if x[1] == "reviewed" and x[2] != "author"]
        state = lambda x: str(x[3].get("state", "")).lower()  # noqa: E731
        author_push = [w for w, k, r, _e in human
                       if r == "author" and k in ("committed", "head_ref_force_pushed")]
        first_start = self.segs[0][0]
        labels: set[str] = set()
        draft = False
        for w, k, r, ev in items:
            name = str((ev.get("label") or {}).get("name", "")).lower()
            if k == "labeled":
                labels.add(name)
            elif k == "unlabeled":
                labels.discard(name)
            elif k == "convert_to_draft":
                draft = True
            elif k == "ready_for_review":
                draft = False
        return {
            "time_since_start_h": round((t - start).total_seconds() / 3600, 2),
            "time_since_last_human_activity_h": round((t - last_human).total_seconds() / 3600, 2),
            "maintainer_replied": bool(maint),
            "first_maintainer_reply_h": (round((maint[0][0] - first_start).total_seconds() / 3600, 2)
                                         if maint else None),
            "review_rounds": len(reviews),
            "changes_requested_count": sum(1 for x in reviews if state(x) == "changes_requested"),
            "approvals_count": sum(1 for x in reviews if state(x) == "approved"),
            "commits_since_start": sum(1 for w, k, r, ev in items if k == "committed"
                                       and w > first_start and (ev.get("parents") or 0) <= 1),
            "force_pushes_since_start": sum(1 for w, k, *_ in items
                                            if k == "head_ref_force_pushed" and w > first_start),
            "author_pushed_after_last_review": (bool(reviews) and any(w > reviews[-1][0]
                                                                      for w in author_push)),
            "maintainer_comment_count": sum(1 for x in human if x[1] == "commented" and x[2] == "maintainer"),
            "author_comment_count": sum(1 for x in human if x[1] == "commented" and x[2] == "author"),
            "other_comment_count": sum(1 for x in human if x[1] == "commented" and x[2] == "other"),
            "has_outcome_label": any(_is_outcome_label(n, self.st) for n in labels),
            "marked_duplicate_so_far": any(k == "marked_as_duplicate" for _w, k, *_ in items),
            "is_draft_now": draft,
            "reopen_count": sum(1 for _w, k, *_ in items if k == "reopened"),
        }

    def row_at(self, t: datetime, trigger: str, role: str) -> dict:
        row = dict(self.at_open)
        sha, found = self.head_at(t)
        code, base = self.code_at(sha)
        row.update(code)
        if self.hist_pr is not None:
            row.update(self.history.at(self.hist_pr, t, self.st, with_unmerged=True))
        row.update(self.state_at(t))
        key = f"{row.get('repo')}#{row.get('pr_number')}#{trigger}#{_iso(t)}"
        row.update({
            "snapshot_id": hashlib.sha1(key.encode()).hexdigest()[:16],
            "snapshot_at": _iso(t), "trigger": trigger, "trigger_role": role,
            "snapshot_head_sha": sha, "snapshot_base_sha": base, "head_found": found,
            "near_decided": bool((self.close and t >= self.close - NEAR_CLOSE)
                                 or (self.merge_queue_at and t >= self.merge_queue_at)),
        })
        return row


_KEY_STATE = ("snapshot_head_sha", "review_rounds", "changes_requested_count", "approvals_count",
              "maintainer_comment_count", "author_comment_count", "other_comment_count",
              "has_outcome_label", "is_draft_now", "marked_duplicate_so_far")


def build(record: dict, at_open: dict, history: merge_signals.History,
          st: config.Settings, evidence: maintainers.Evidence | None = None) -> list[dict]:
    """The kept snapshots of one PR, in time order."""
    ctx = Context(record, at_open, history, st, evidence)
    if not ctx.segs:
        return []
    cands = [c for c in debounce(candidates(record, ctx.tl, ctx.segs, st))
             if ctx.close is None or c[0] < ctx.close]
    rows: list[dict] = []
    for t, trig, role in cands:
        r = ctx.row_at(t, trig, role)
        if rows:
            prev = rows[-1]
            same = all(r.get(k) == prev.get(k) for k in _KEY_STATE)
            if same and trig not in ("clock", "idle", "no_maintainer_reply"):
                continue                      # nothing new happened
            if same and t - _ts(prev["snapshot_at"]) <= NEIGHBOUR:
                if PRIORITY[trig] < PRIORITY[prev["trigger"]]:
                    rows[-1] = r
                continue
        rows.append(r)
    keep = sorted(range(len(rows)), key=lambda i: (PRIORITY[rows[i]["trigger"]], i))[:MAX_PER_PR]
    rows = [rows[i] for i in sorted(keep)]
    prev = None
    for i, r in enumerate(rows):
        r["snapshot_index"] = i
        r["snapshot_weight"] = round(1 / len(rows), 6)
        r["moment_bucket"] = moment_bucket(r["trigger"], r["time_since_start_h"])
        if prev is None:
            r.update(new_commits_since_prev=None, new_comments_since_prev=None,
                     diff_size_change_since_prev=None)
        else:
            r["new_commits_since_prev"] = r["commits_since_start"] - prev["commits_since_start"]
            r["new_comments_since_prev"] = sum(
                r[c] - prev[c] for c in ("maintainer_comment_count", "author_comment_count",
                                         "other_comment_count"))
            size = lambda x: ((x.get("LA") or 0) + (x.get("LD") or 0))  # noqa: E731
            r["diff_size_change_since_prev"] = size(r) - size(prev)
        prev = r
    return rows


_COMMIT_BASE = ("repo", "pr_number", "head_sha", "base_sha", "first_seen", "at_open", "error")


def commit_feature_rows(record: dict) -> list[dict]:
    out = []
    for h in record.get("heads") or []:
        out.append({"repo": record.get("repo"), "pr_number": record.get("pr_number"),
                    "head_sha": h["sha"], "base_sha": h.get("base_sha"),
                    "first_seen": h.get("first_seen"), "at_open": bool(h.get("at_open")),
                    "error": h.get("error"), **(h.get("features") or {})})
    return out


# ---- files -------------------------------------------------------------------------

def _read_rows(path: str) -> list[dict]:
    if path.endswith(".parquet"):
        import pandas as pd
        df = pd.read_parquet(path)
        return json.loads(df.to_json(orient="records", date_format="iso"))
    return cap.read_jsonl(path)


def _write_rows(rows: list[dict], path: str) -> None:
    if path.endswith(".parquet"):
        import pandas as pd
        pd.DataFrame(rows).to_parquet(path, index=False)
    else:
        with open(path, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, default=str) + "\n")


def assemble(dataset_rows: list[dict], raw_root: str, st: config.Settings) -> tuple[list, list]:
    at_open = {(r.get("repo"), r.get("pr_number")): r for r in dataset_rows}
    snaps, commits = [], []
    for d in sorted(glob.glob(os.path.join(raw_root, "*"))):
        records = cap.read_jsonl(os.path.join(d, cap.CAPTURE_FILE))
        if not records:
            continue
        history = merge_signals.History(cap.read_jsonl(os.path.join(d, cap.INDEX_FILE)))
        evidence = maintainers.Evidence.load(d)
        for rec in records:
            row = at_open.get((rec.get("repo"), rec.get("pr_number")))
            commits.extend(commit_feature_rows(rec))
            if row is not None:
                snaps.extend(build(rec, row, history, st, evidence))
    # every commit row carries every feature column (null where not computed)
    cols = sorted({k for r in commits for k in r} - set(_COMMIT_BASE))
    commits = [{**{c: None for c in cols}, **r} for r in commits]
    return snaps, commits


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", required=True, help="mined at-open rows (.parquet or .jsonl)")
    ap.add_argument("--raw-root", default="snapshots_raw")
    ap.add_argument("--out", default="snapshots.parquet")
    ap.add_argument("--commit-features-out", default="commit_features.parquet")
    args = ap.parse_args()
    st = config.Settings()
    snaps, commits = assemble(_read_rows(args.dataset), args.raw_root, st)
    _write_rows(snaps, args.out)
    _write_rows(commits, args.commit_features_out)
    prs = len({(r["repo"], r["pr_number"]) for r in snaps})
    print(f"[snapshots] {len(snaps)} snapshots from {prs} PRs -> {args.out}; "
          f"{len(commits)} head commits -> {args.commit_features_out}", file=sys.stderr)


if __name__ == "__main__":
    main()
