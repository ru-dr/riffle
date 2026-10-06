"""
repo_local.py — the repo-local mined features of contracts/mined_features.schema.json.

Nine columns capturing how THIS repository behaves around the paths a PR
touches: similar past PRs and how they went, how failures on these paths
surfaced, how much review scrutiny the paths draw, how big this change is for
these files, and where the repo is in its release cycle.

Two stages:
  - per PR, in mine_pr (worker thread): sidecar() records what later PRs need
    (open time, touched files, review events) and path_size_pctile() scores
    this PR against past commits to the same files at the base commit.
  - per repo, after all PRs are mined: compute_repo() fills the PR-history
    columns for every row.

Point-in-time rule (contract): for a PR opened at T, a past PR contributes only
if it merged before T AND its labels were mature at T (merged at least
labels.maturity_days before T); its outcome is what was observable at T (an
outcome event after T doesn't count). Only reviews submitted before T count.
null means not enough history (thresholds in Settings, mirroring
rules.mining), never 0.
"""
from __future__ import annotations

import bisect
import re
import statistics
from datetime import datetime, timedelta, timezone

import config
import gitio

COLUMNS = (
    "similar_pr_bad_rate", "similar_pr_count", "revealed_path_scrutiny",
    "path_failure_mode_revert", "path_failure_mode_ci", "path_failure_mode_hotfix",
    "path_detection_lag_days", "path_size_pctile", "release_cycle_position",
)

SIDECAR = "_repo_local"        # private row key; stripped before the dataset is written


def _ts(s) -> datetime | None:
    if not s:
        return None
    if isinstance(s, datetime):
        return s if s.tzinfo else s.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


# ---------------------------------------------------------------- per PR
def sidecar(pr: dict, numstat: list[tuple[int, int, str]], reviews: dict | None) -> dict:
    """What later PRs need to know about this one (kept in the checkpoint)."""
    files: dict[str, int] = {}
    for a, d, p in numstat:
        files[p] = files.get(p, 0) + a + d
    return {
        "opened_at": pr.get("created_at") or pr.get("merged_at"),
        "files": files,
        "reviews": reviews,
    }


def path_size_pctile(repo: str, base: str, numstat: list[tuple[int, int, str]],
                     st: config.Settings) -> float | None:
    """
    Percentile of this PR's LA+LD among past commits (before the base commit,
    within the lookback) that touched the same files. Each past commit counts
    once per touched file it shares, weighted by this PR's lines in that file.
    Ties count half. null with fewer than min_prior_changes distinct commits.
    """
    size = sum(a + d for a, d, _ in numstat)
    weights: dict[str, int] = {}
    for a, d, p in numstat:
        weights[p] = weights.get(p, 0) + a + d
    if not weights:
        return None
    if not any(weights.values()):
        weights = {p: 1 for p in weights}
    base_time = gitio.commit_time(repo, base)
    start = base_time - timedelta(days=st.path_size_lookback_days)
    entries = gitio.iter_commits_with_files(repo, base, before=base_time)
    below = ties = total = 0.0
    seen: set[str] = set()
    for c, rows in entries:
        if c.committed < start:
            continue
        shared = {p for _, _, p in rows if p in weights}
        if not shared:
            continue
        seen.add(c.sha)
        csize = sum(a + d for a, d, _ in rows)
        w = sum(weights[p] for p in shared)
        total += w
        if csize < size:
            below += w
        elif csize == size:
            ties += w
    if len(seen) < st.path_size_min_prior_changes or total <= 0:
        return None
    return round((below + 0.5 * ties) / total, 4)


def release_tags(repo: str, st: config.Settings) -> list[datetime]:
    """Sorted, de-duplicated release times (tags matching release_tag_pattern;
    tagger date for annotated tags, commit date for lightweight ones)."""
    rx = re.compile(st.release_tag_pattern)
    out = gitio._run(repo, "for-each-ref", "refs/tags",
                     "--format=%(refname:short)%09%(creatordate:unix)", check=False)
    times = set()
    for line in out.splitlines():
        name, _, ts = line.partition("\t")
        if rx.search(name) and ts.strip().isdigit():
            times.add(datetime.fromtimestamp(int(ts), tz=timezone.utc))
    return sorted(times)


def _release_cycle_position(t: datetime, releases: list[datetime],
                            st: config.Settings) -> float | None:
    prior = releases[:bisect.bisect_left(releases, t)]
    if len(prior) < st.release_min_releases:
        return None
    gaps = [(b - a).total_seconds() / 86400 for a, b in zip(prior, prior[1:])]
    gaps = [g for g in gaps if g > 0]
    if not gaps:
        return None
    typical = statistics.median(gaps) if st.release_interval_stat == "median" \
        else statistics.fmean(gaps)
    if typical <= 0:
        return None
    since = (t - prior[-1]).total_seconds() / 86400
    return round(since / typical, 4)


# ---------------------------------------------------------------- per repo
def _outcome_at(row: dict, merged: datetime, t: datetime) -> tuple[str, float] | None:
    """(primary failure mode, detection lag days) as observable at time t, or
    None if the PR looked clean at t. Primary = the earliest outcome event."""
    events = []
    if row.get("label_ci_fail") is True:
        events.append(("ci_fail", 0.0))                  # CI verdict lands at merge
    for mode, label, lag_key in (("revert", "label_revert", "days_to_revert"),
                                 ("hotfix", "label_hotfix", "days_to_hotfix")):
        lag = row.get(lag_key)
        if row.get(label) is True and lag is not None and \
                merged + timedelta(days=float(lag)) <= t:
            events.append((mode, float(lag)))
    if not events:
        return None
    return min(events, key=lambda e: e[1])


def _parse_reviews(reviews: dict | None) -> dict | None:
    if reviews is None:
        return None
    return {k: sorted(d for d in (_ts(x) for x in reviews.get(k, [])) if d)
            for k in ("comments", "changes_requested", "rounds")}


def _scrutiny_score(reviews: dict, t: datetime) -> float:
    """comments + 2 x changes_requested + rounds, counting only events before t."""
    def n(key):
        return bisect.bisect_left(reviews[key], t)
    return n("comments") + 2 * n("changes_requested") + n("rounds")


def compute_repo(rows: list[dict], releases: list[datetime], st: config.Settings) -> None:
    """Fill the PR-history columns on every row in place (path_size_pctile was
    set per PR). Rows without a sidecar keep whatever they have."""
    pool = []
    for r in rows:
        sc = r.get(SIDECAR)
        merged = _ts(r.get("merged_at"))
        if not sc or merged is None:
            continue
        pool.append({"row": r, "num": r.get("pr_number"), "merged": merged,
                     "opened": _ts(sc.get("opened_at")) or merged,
                     "files": set(sc.get("files") or {}),
                     "reviews": _parse_reviews(sc.get("reviews"))})
    pool.sort(key=lambda q: q["merged"])
    merged_times = [q["merged"] for q in pool]
    by_file: dict[str, list[int]] = {}
    for i, q in enumerate(pool):
        for f in q["files"]:
            by_file.setdefault(f, []).append(i)

    for p in pool:
        row, t, files = p["row"], p["opened"], p["files"]
        row.update(_empty_history_cols())
        row["release_cycle_position"] = _release_cycle_position(t, releases, st)
        # past PRs merged strictly before this PR opened
        n_before = bisect.bisect_left(merged_times, t)
        mature_cut = t - timedelta(days=st.maturity_days)
        cand = sorted({i for f in files for i in by_file.get(f, []) if i < n_before})

        # --- similar PRs: k nearest by Jaccard over touched files
        sim_start = t - timedelta(days=st.similar_prs_lookback_days)
        neigh = []
        for i in cand:
            q = pool[i]
            if q["merged"] > mature_cut or q["merged"] < sim_start:
                continue
            ov = len(files & q["files"]) / len(files | q["files"])
            if ov >= st.similar_prs_min_file_overlap:
                neigh.append((ov, q["merged"], q))
        neigh.sort(key=lambda x: (x[0], x[1]), reverse=True)
        neigh = neigh[: st.similar_prs_k]
        n = len(neigh)
        row["similar_pr_count"] = n
        if n >= st.similar_prs_min_neighbours:
            bad = sum(1 for _, _, q in neigh if _outcome_at(q["row"], q["merged"], t))
            prior = row.get("repo_revert_base_rate") or 0.0
            # missing neighbours (k - n) are filled with the repo base rate
            k = st.similar_prs_k
            row["similar_pr_bad_rate"] = round((bad + (k - n) * prior) / k, 4)

        # --- failure mode + detection lag on these paths
        fm_start = t - timedelta(days=st.failure_mode_lookback_days)
        modes = []
        for i in cand:
            q = pool[i]
            if q["merged"] > mature_cut or q["merged"] < fm_start:
                continue
            o = _outcome_at(q["row"], q["merged"], t)
            if o:
                modes.append(o)
        if len(modes) >= st.failure_mode_min_bad_changes:
            for mode, col in (("revert", "path_failure_mode_revert"),
                              ("ci_fail", "path_failure_mode_ci"),
                              ("hotfix", "path_failure_mode_hotfix")):
                row[col] = round(sum(1 for m, _ in modes if m == mode) / len(modes), 4)
            row["path_detection_lag_days"] = round(statistics.median(l for _, l in modes), 2)

        # --- revealed path scrutiny (reviews before T; no maturity needed)
        sc_start = t - timedelta(days=st.scrutiny_lookback_days)
        window = [q for q in pool[:n_before]
                  if q["merged"] >= sc_start and q["reviews"] is not None]
        if len(window) >= st.scrutiny_min_prior_prs:
            scores = {id(q): _scrutiny_score(q["reviews"], t) for q in window}
            repo_mean = statistics.fmean(scores.values())
            file_means = []
            for f in files:
                vals = [scores[id(q)] for q in window if f in q["files"]]
                if len(vals) >= st.scrutiny_min_prior_prs:
                    file_means.append(statistics.fmean(vals))
            if file_means and repo_mean > 0:
                row["revealed_path_scrutiny"] = round(statistics.fmean(file_means) / repo_mean, 4)


def _empty_history_cols() -> dict:
    return {"similar_pr_bad_rate": None, "similar_pr_count": 0,
            "revealed_path_scrutiny": None, "path_failure_mode_revert": None,
            "path_failure_mode_ci": None, "path_failure_mode_hotfix": None,
            "path_detection_lag_days": None, "release_cycle_position": None}
