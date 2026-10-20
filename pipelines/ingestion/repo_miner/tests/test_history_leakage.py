"""History features are point-in-time (proposal 1.5).

An earlier PR's outcome may count only once it is knowable when the PR being
scored opened: the earlier PR closed (merged, for the breakage history)
before that moment, and its outcome event happened before it too. For every
PR in a random synthetic repo, each history column is recomputed from a world
cut at that PR's open (later PRs, closes, outcomes, reviews, first responses
and release tags removed) and must not change.
"""
from __future__ import annotations

import os
import random
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from features import merge_signals, repo_local  # noqa: E402

UTC = timezone.utc
T0 = datetime(2025, 1, 1, tzinfo=UTC)
FILES = ["src/a.ts", "src/b.ts", "src/c.ts", "lib/d.ts", "docs/e.md"]


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def world(seed=7, n=80):
    rnd = random.Random(seed)
    prs = []
    for i in range(n):
        opened = T0 + timedelta(days=rnd.uniform(0, 300))
        closed = opened + timedelta(hours=rnd.uniform(1, 24 * 40))
        merged = rnd.random() < 0.65
        files = {f: rnd.randint(1, 80) for f in rnd.sample(FILES, rnd.randint(1, 3))}
        reviews = {k: sorted(iso(opened + timedelta(hours=rnd.uniform(0, 24 * 20)))
                             for _ in range(rnd.randint(0, 4)))
                   for k in ("comments", "changes_requested", "rounds")}
        revert = merged and rnd.random() < 0.2
        hotfix = merged and not revert and rnd.random() < 0.15
        prs.append({
            "number": i + 1, "opened": opened, "closed": closed, "merged": merged,
            "files": files, "reviews": reviews,
            "label_revert": revert, "days_to_revert": rnd.uniform(0.5, 25) if revert else None,
            "label_hotfix": hotfix, "days_to_hotfix": rnd.uniform(0.5, 6) if hotfix else None,
            "label_ci_fail": merged and rnd.random() < 0.1,
            "outsider": rnd.random() < 0.6, "agent": rnd.choice([None, None, "codex", "copilot"]),
            "first_response": (opened + timedelta(hours=rnd.uniform(0.5, 72))) if rnd.random() < 0.7 else None,
            "tokens": rnd.sample(["fix", "add", "api", "docs", "test", "ui", "perf"], 3),
        })
    releases = sorted(T0 + timedelta(days=rnd.uniform(-200, 320)) for _ in range(12))
    return prs, releases


def _st():
    st = config.Settings()
    st.merge_signal_min_prs = 3
    st.similar_prs_min_neighbours = 1
    st.failure_mode_min_bad_changes = 1
    st.scrutiny_min_prior_prs = 2
    return st


# ---- repo-local (breakage history) -----------------------------------------------

def _rows(prs, t=None):
    """Mined rows as compute_repo sees them; with t, what was knowable at t."""
    rows = []
    for p in prs:
        merged_at = p["closed"] if p["merged"] else None
        row = {"pr_number": p["number"], "merged_at": iso(merged_at) if merged_at else None,
               "repo_revert_base_rate": 0.1, "label_ci_fail": p["label_ci_fail"],
               "label_revert": p["label_revert"], "days_to_revert": p["days_to_revert"],
               "label_hotfix": p["label_hotfix"], "days_to_hotfix": p["days_to_hotfix"]}
        reviews = p["reviews"]
        if t is not None:
            if merged_at is not None and merged_at >= t:
                row["merged_at"] = None                      # not merged yet at t
            for lab, lag in (("label_revert", "days_to_revert"), ("label_hotfix", "days_to_hotfix")):
                if row[lag] is not None and merged_at + timedelta(days=row[lag]) > t:
                    row[lab], row[lag] = False, None         # not reverted / hotfixed yet at t
            if merged_at is None or merged_at >= t:
                row["label_ci_fail"] = False
            reviews = {k: [x for x in v if ts(x) < t] for k, v in reviews.items()}
        row[repo_local.SIDECAR] = {"opened_at": iso(p["opened"]), "files": p["files"],
                                   "open_files": p["files"], "reviews": reviews}
        rows.append(row)
    return rows


def test_repo_local_history_is_point_in_time():
    prs, releases = world()
    st = _st()
    full = _rows(prs)
    repo_local.compute_repo(full, releases, st)
    checked = 0
    for p, row in zip(prs, full):
        t = p["opened"]
        cut = _rows([q for q in prs if q["opened"] <= t], t=t)
        repo_local.compute_repo(cut, [r for r in releases if r < t], st)
        mine = next(r for r in cut if r["pr_number"] == p["number"])
        for col in repo_local.COLUMNS:
            if col == "path_size_pctile":
                continue                                     # per PR, from git at the base
            assert row.get(col) == mine.get(col), (p["number"], col, row.get(col), mine.get(col))
        checked += row.get("similar_pr_count") or 0
    assert checked > 0                                       # the history wasn't all empty


# ---- merge signals (merge-model history) ------------------------------------------

def _sidecars(prs, t=None):
    out = []
    for p in prs:
        closed, merged, first = p["closed"], p["merged"], p["first_response"]
        if t is not None:
            if closed >= t:
                closed, merged = None, None                  # still open at t
            if first is not None and first >= t:
                first = None
        out.append({"number": p["number"], "opened": iso(p["opened"]),
                    "closed": iso(closed) if closed else None, "merged": merged,
                    "outsider": p["outsider"], "agent": p["agent"],
                    "first_response": iso(first) if first else None, "tokens": p["tokens"]})
    return out


def test_merge_signals_are_point_in_time():
    prs, _ = world(seed=11)
    st = _st()
    full = merge_signals.History(_sidecars(prs))
    nonempty = 0
    for p in full.prs:
        t = p["opened"]
        cut = merge_signals.History(_sidecars(prs, t=t))
        q = next(x for x in cut.prs if x["number"] == p["number"])
        a, b = full.at(p, t, st, True), cut.at(q, t, st, True)
        assert a == b, (p["number"], {k: (a[k], b[k]) for k in a if a[k] != b[k]})
        nonempty += a["repo_outside_merge_rate"] is not None
    assert nonempty > 0


def test_merge_signals_lead_in_counts_only_what_closed_before():
    """Lead-in PRs (before the mined block) feed the first PRs' history."""
    prs, _ = world(seed=3)
    st = _st()
    first = min(prs, key=lambda p: p["opened"])
    lead = [dict(s, number=1000 + i, opened=iso(ts(s["opened"]) - timedelta(days=400)),
                 closed=iso(ts(s["closed"]) - timedelta(days=400)) if s["closed"] else None,
                 first_response=None)
            for i, s in enumerate(_sidecars(prs)[:10])]
    rows = [{merge_signals.SIDECAR: s} for s in _sidecars(prs)]
    merge_signals.compute_repo(rows, st, with_unmerged=True)
    without = next(r for r in rows if r[merge_signals.SIDECAR]["number"] == first["number"])
    rows = [{merge_signals.SIDECAR: s} for s in _sidecars(prs)]
    merge_signals.compute_repo(rows, st, with_unmerged=True, lead_in=lead)
    with_lead = next(r for r in rows if r[merge_signals.SIDECAR]["number"] == first["number"])
    assert without["repo_outside_closed_count"] == 0
    assert with_lead["repo_outside_closed_count"] == sum(1 for s in lead if s["outsider"])
