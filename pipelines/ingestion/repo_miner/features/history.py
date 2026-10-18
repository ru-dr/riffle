"""
history.py — Source 2 repo-history features, all as of the PR's base commit.

These are the "knows this repo" signals: per-file revert/fix/churn history,
complexity, hotspots, ownership, coupling, author behavior, repo baselines,
and concurrency. Everything walks commits strictly before the base commit's
time so nothing from the future leaks in.

Per-file values are returned as dicts keyed by path; aggregate.py collapses
them to fixed max/mean/sum columns. Repo-level scalars are returned flat.
"""
from __future__ import annotations

import hashlib
import math
import statistics
from collections import defaultdict, Counter
from datetime import datetime, timedelta, timezone

import config
import gitio


def _is_fix(subject: str, st: config.Settings) -> bool:
    return config.is_fix_text(subject)


def _is_revert(subject: str, st: config.Settings) -> bool:
    return config.is_revert_text(subject)


def _is_hotfix(subject: str, st: config.Settings) -> bool:
    return config.is_hotfix_text(subject)


def _decay(days: float, half_life: float) -> float:
    return 0.5 ** (days / half_life) if half_life > 0 else 0.0


def build_repo_index(repo: str, base: str, st: config.Settings,
                     ci_map: dict | None = None,
                     paths: list[str] | None = None) -> dict:
    """
    One pass over all commits up to `base` building per-file and per-author
    aggregates. Returned index is reused for every file in the PR.

    paths: when given (the PR's changed files), per-file aggregates and the
    co-change matrix are kept only for these paths — the only ones the feature
    functions read — instead of every file in history. On a large repo that is
    the difference between a few entries and millions per PR (one vendor bump
    touching 5k files alone adds 25M co-change pairs). Values for the kept
    paths, author aggregates and the repo-wide hotspot ranking are unchanged.

    ci_map: optional {commit_sha: 'failure'|'success'} from the historical CI
    pre-pass; when present, per-file and per-author post-merge CI-fail rates are
    accumulated point-in-time.
    """
    ci_map = ci_map or {}
    base_time = gitio.commit_time(repo, base)
    entries = gitio.iter_commits_with_files(repo, base, before=base_time)
    n_commits_total = len(entries)

    file_commits: dict[str, list] = defaultdict(list)   # path -> [(time, sha, subj, author)]
    file_authors: dict[str, set] = defaultdict(set)
    file_revert_ct: dict[str, int] = defaultdict(int)
    file_fix_ct: dict[str, int] = defaultdict(int)
    file_decay_fix: dict[str, float] = defaultdict(float)
    file_decay_revert: dict[str, float] = defaultdict(float)
    file_last_revert: dict[str, datetime] = {}
    file_last_fix: dict[str, datetime] = {}
    file_hotfix_365d: dict[str, int] = defaultdict(int)     # hotfix commits, last year
    file_test_cochange: dict[str, int] = defaultdict(int)   # commits where a test co-changed
    file_ci_fail: dict[str, int] = defaultdict(int)         # commits to file whose CI failed
    file_ci_total: dict[str, int] = defaultdict(int)        # commits to file with a CI verdict
    cochange: dict[str, Counter] = defaultdict(Counter)  # type: ignore

    year_ago = base_time - timedelta(days=365)
    keep = None if paths is None else set(paths)
    # commits per file in the last 365d, for EVERY file (the hotspot ranking is
    # repo-wide). A key is added on a file's first appearance, so iteration
    # order — and with it the tie order in the ranking — matches file_commits.
    churn365: dict[str, int] = {}

    author_commits: dict[str, int] = defaultdict(int)
    author_last: dict[str, datetime] = {}
    author_revert: dict[str, int] = defaultdict(int)
    author_total_authored: dict[str, int] = defaultdict(int)
    author_rexp: dict[str, float] = defaultdict(float)          # recency-weighted exp
    author_subsys: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    author_ci_fail: dict[str, int] = defaultdict(int)
    author_ci_total: dict[str, int] = defaultdict(int)

    # repo-wide distributions (per non-merge commit) for percentile features
    size_pop: list[int] = []
    nf_pop: list[int] = []
    entropy_pop: list[float] = []

    repo_revert_total = 0

    for c, rows in entries:
        paths = [p for _, _, p in rows]
        is_fix = _is_fix(c.subject, st)
        is_rev = _is_revert(c.subject, st)
        is_hotfix = _is_hotfix(c.subject, st)
        commit_has_test = any(config.path_flags(p)["is_test"] for p in paths)
        ci_verdict = ci_map.get(c.sha)                  # 'failure' | 'success' | None
        if ci_verdict in ("failure", "success"):
            author_ci_total[c.author_email] += 1
            if ci_verdict == "failure":
                author_ci_fail[c.author_email] += 1
        if is_rev:
            repo_revert_total += 1
            author_revert[c.author_email] += 1
        author_commits[c.author_email] += 1
        author_total_authored[c.author_email] += 1
        if c.author_email not in author_last or c.committed > author_last[c.author_email]:
            author_last[c.author_email] = c.committed
        age_days = (base_time - c.committed).days

        # author experience: recency-weighted (Kamei REXP) + per-subsystem (SEXP)
        author_rexp[c.author_email] += 1.0 / (max(age_days, 0) / 365.0 + 1.0)
        for sub in {config.subsystem_of(p) for p in paths}:
            author_subsys[c.author_email][sub] += 1

        # repo size/spread distribution (skip empty/merge commits)
        if rows:
            size_pop.append(sum(a + d for a, d, _ in rows))
            nf_pop.append(len(paths))
            entropy_pop.append(_commit_entropy(rows))

        recent = c.committed >= year_ago
        for p in paths:
            if p not in churn365:
                churn365[p] = 0
            if recent:
                churn365[p] += 1
            if keep is not None and p not in keep:
                continue
            file_commits[p].append((c.committed, c.sha, c.subject, c.author_email))
            file_authors[p].add(c.author_email)
            if is_hotfix and c.committed >= year_ago:
                file_hotfix_365d[p] += 1
            if commit_has_test and not config.path_flags(p)["is_test"]:
                file_test_cochange[p] += 1
            if ci_verdict in ("failure", "success"):
                file_ci_total[p] += 1
                if ci_verdict == "failure":
                    file_ci_fail[p] += 1
            if is_rev:
                file_revert_ct[p] += 1
                file_decay_revert[p] += _decay(age_days, st.decay_half_life_days)
                if p not in file_last_revert or c.committed > file_last_revert[p]:
                    file_last_revert[p] = c.committed
            if is_fix:
                file_fix_ct[p] += 1
                file_decay_fix[p] += _decay(age_days, st.decay_half_life_days)
                if p not in file_last_fix or c.committed > file_last_fix[p]:
                    file_last_fix[p] = c.committed
        # co-change: files touched together
        for p in paths:
            if keep is not None and p not in keep:
                continue
            for q in paths:
                if p != q:
                    cochange[p][q] += 1

    # --- repo-wide hotspot ranking (point-in-time, as of base) --------------
    # Hotspot = churn(365d) x complexity. Rank EVERY file in the repo, not just
    # the changed ones, so `touches_topk_hotspot` is a real repo-relative signal
    # rather than a per-PR proxy. Only files with recent churn can be hotspots,
    # so we score complexity for just those (a file with zero recent churn has
    # hotspot 0 whatever its complexity) — keeps this cheap on large repos.
    hotspot_scores: dict[str, float] = {}
    blobs = gitio.tree_blobs(repo, base)
    for p, churn in churn365.items():
        if churn <= 0:
            continue
        if config.lang_of(p) is None:   # lizard can't score it: skip the read
            continue
        blob = blobs.get(p)
        if blob is None:             # file not present at base (deleted/renamed)
            continue
        ccn = _blob_ccn(repo, blob, p)
        if not ccn:                  # None (no tool / unsupported) or 0
            continue
        hotspot_scores[p] = churn * ccn

    ranked = sorted(hotspot_scores.items(), key=lambda kv: kv[1], reverse=True)
    hotspot_rank = {p: i + 1 for i, (p, _) in enumerate(ranked)}       # 1 = hottest
    hotspot_topk = {p for p, _ in ranked[: st.hotspot_top_k]}

    return {
        "base_time": base_time,
        "file_commits": file_commits,
        "file_authors": file_authors,
        "file_revert_ct": file_revert_ct,
        "file_fix_ct": file_fix_ct,
        "file_decay_fix": file_decay_fix,
        "file_decay_revert": file_decay_revert,
        "file_last_revert": file_last_revert,
        "file_last_fix": file_last_fix,
        "file_hotfix_365d": file_hotfix_365d,
        "file_test_cochange": file_test_cochange,
        "file_ci_fail": file_ci_fail,
        "file_ci_total": file_ci_total,
        "author_ci_fail": author_ci_fail,
        "author_ci_total": author_ci_total,
        "cochange": cochange,
        "author_commits": author_commits,
        "author_last": author_last,
        "author_revert": author_revert,
        "author_total_authored": author_total_authored,
        "author_rexp": author_rexp,
        "author_subsys": author_subsys,
        "size_pop": size_pop,
        "nf_pop": nf_pop,
        "entropy_pop": entropy_pop,
        "repo_revert_base_rate": (repo_revert_total / n_commits_total) if n_commits_total else 0.0,
        "n_commits_total": n_commits_total,
        "hotspot_scores": hotspot_scores,
        "hotspot_rank": hotspot_rank,
        "hotspot_topk": hotspot_topk,
    }


def per_file_features(repo: str, base: str, path: str, idx: dict,
                      st: config.Settings) -> dict:
    """Source 2b/2c/2d/2e per-file features for one changed file, as of base."""
    base_time = idx["base_time"]
    commits = idx["file_commits"].get(path, [])
    n = len(commits)
    revert_ct = idx["file_revert_ct"].get(path, 0)
    fix_ct = idx["file_fix_ct"].get(path, 0)
    times = [t for t, _, _, _ in commits]

    last_change = max(times) if times else None
    age_days = (base_time - min(times)).days if times else 0.0
    last_change_days = (base_time - last_change).days if last_change else None

    # Ownership by LINES, not commits: blame the file at base and count who
    # wrote each surviving line. This is the true line-level ownership the
    # reference specifies (Bird et al.), replacing the earlier commit-share
    # proxy. Powers file_top_author_share, file_minor_contributor_count, and
    # abandoned_lines_share. The blame call is cached (gitio.blame_file), so
    # sharing it with the Source-3 line features costs nothing extra.
    blame = gitio.blame_file(repo, base, path)
    line_authors = [bl.author_email for bl in blame.values()]
    total_lines = len(line_authors)
    if total_lines:
        la_counts = Counter(line_authors)
        top_share = la_counts.most_common(1)[0][1] / total_lines
        minor = sum(1 for _, cnt in la_counts.items() if cnt / total_lines < 0.05)
        # abandoned = share of LINES whose author is inactive >180d as of base
        abandoned_lines = 0
        for email, cnt in la_counts.items():
            la = idx["author_last"].get(email)
            if la and (base_time - la).days > 180:
                abandoned_lines += cnt
        abandoned_share = abandoned_lines / total_lines
    else:
        # binary/empty/unblameable file: no line ownership to report
        top_share, minor, abandoned_share = 0.0, 0, 0.0

    last_rev = idx["file_last_revert"].get(path)
    last_fix = idx["file_last_fix"].get(path)

    # complexity at base
    ccn = _lizard_ccn(repo, base, path)

    feats = {
        "file_revert_density": revert_ct / n if n else 0.0,
        "revert_decay_density": idx["file_decay_revert"].get(path, 0.0),
        "days_since_last_revert": (base_time - last_rev).days if last_rev else None,
        "file_fix_density": fix_ct / n if n else 0.0,
        "fix_decay_density": idx["file_decay_fix"].get(path, 0.0),
        "days_since_last_fix": (base_time - last_fix).days if last_fix else None,
        "file_churn_90d": _windowed_churn(times, base_time, 90),
        "file_churn_365d": _windowed_churn(times, base_time, 365),
        "recent_merge_burst_7d": _windowed_churn(times, base_time, 7),
        "hotfix_count_365d": idx["file_hotfix_365d"].get(path, 0),
        "test_cochange_ratio": round(idx["file_test_cochange"].get(path, 0) / n, 4) if n else 0.0,
        "file_post_merge_ci_fail_rate": (
            round(idx["file_ci_fail"].get(path, 0) / idx["file_ci_total"][path], 4)
            if idx["file_ci_total"].get(path) else None),
        "file_age_days": age_days,
        "NUC": n,                                   # unique prior changes
        "NDEV": len(idx["file_authors"].get(path, set())),
        "file_complexity_ccn": ccn,
        "file_top_author_share": round(top_share, 4),
        "file_minor_contributor_count": minor,
        "abandoned_lines_share": round(abandoned_share, 4),
        "_last_change_days": last_change_days,      # internal, for AGE
        "_loc": gitio.file_loc_at(repo, base, path),  # for LT / hotspot
    }
    # hotspot score = churn(365) * complexity  (classic)
    feats["file_hotspot_score"] = feats["file_churn_365d"] * (ccn or 0)
    # repo-relative rank of this file among all repo hotspots (1 = hottest);
    # None if the file has no recent churn / complexity to rank on.
    feats["file_hotspot_rank"] = idx.get("hotspot_rank", {}).get(path)
    # co-change degree + missing partners handled at PR level (needs the diff set)
    return feats


def _windowed_churn(times: list[datetime], base_time: datetime, days: int) -> int:
    cutoff = base_time - timedelta(days=days)
    return sum(1 for t in times if t >= cutoff)


def _lizard_ccn(repo: str, sha: str, path: str) -> float | None:
    """Max cyclomatic complexity of the file at `sha`, via lizard (optional)."""
    content = gitio.file_content_at(repo, sha, path)
    if content is None:
        return None
    return _lizard_ccn_from_content(content, path)


# complexity per (blob sha, path): a file version shared by many PRs' base
# commits is read from git and scored once
_BLOB_CCN_CACHE: dict[tuple[str, str], float | None] = {}


def _blob_ccn(repo: str, blob: str, path: str) -> float | None:
    key = (blob, path)
    if key not in _BLOB_CCN_CACHE:
        content = gitio._run(repo, "cat-file", "blob", blob, check=False)
        _BLOB_CCN_CACHE[key] = _lizard_ccn_from_content(content, path)
    return _BLOB_CCN_CACHE[key]


# cache complexity by (content-hash, path) so a file version that is identical
# across many PRs' base commits is parsed only once.
_CCN_CACHE: dict[tuple[str, str], float | None] = {}


def _lizard_ccn_from_content(content: str, path: str) -> float | None:
    """Max cyclomatic complexity for file `content`, cached by content hash."""
    lang = config.lang_of(path)
    if lang is None:
        return None
    h = hashlib.md5(content.encode("utf-8", "replace")).hexdigest()
    key = (h, path)
    if key in _CCN_CACHE:
        return _CCN_CACHE[key]
    try:
        import lizard
    except Exception:
        _CCN_CACHE[key] = None
        return None
    try:
        analysis = lizard.analyze_file.analyze_source_code(path, content)
        val = (0.0 if not analysis.function_list
               else max(f.cyclomatic_complexity for f in analysis.function_list))
    except Exception:
        val = None
    _CCN_CACHE[key] = val
    return val


def author_features(author_email: str, touched_paths: list[str], idx: dict,
                    st: config.Settings) -> dict:
    """Source 2g author-behavior features, smoothed toward repo mean."""
    a = author_email.lower()
    total = idx["author_total_authored"].get(a, 0)
    reverts = idx["author_revert"].get(a, 0)
    base_rate = idx["repo_revert_base_rate"]
    alpha = st.author_smoothing_alpha
    # empirical-Bayes shrink toward repo base rate
    smoothed = (reverts + alpha * base_rate) / (total + alpha) if (total + alpha) else base_rate

    fam_hits = sum(1 for p in touched_paths if a in idx["file_authors"].get(p, set()))
    familiarity = fam_hits / len(touched_paths) if touched_paths else 0.0

    last = idx["author_last"].get(a)
    days_since = (idx["base_time"] - last).days if last else None
    # post-merge CI-fail rate, empirical-Bayes smoothed toward repo mean; None
    # when we have no CI history for this author.
    ci_total = idx["author_ci_total"].get(a, 0)
    ci_fail = idx["author_ci_fail"].get(a, 0)
    repo_ci_total = sum(idx["author_ci_total"].values())
    repo_ci_fail = sum(idx["author_ci_fail"].values())
    if ci_total > 0:
        repo_ci_rate = (repo_ci_fail / repo_ci_total) if repo_ci_total else 0.0
        ci_fail_rate = round((ci_fail + alpha * repo_ci_rate) / (ci_total + alpha), 4)
    else:
        ci_fail_rate = None
    return {
        "author_revert_rate": round(smoothed, 4),
        "author_file_familiarity": round(familiarity, 4),
        "author_days_since_last_change": days_since,
        "author_post_merge_ci_fail_rate": ci_fail_rate,
        # author_prs_merged_repo is set in the orchestrator from the PR list
        # (real merged-PR count, point-in-time), not here.
    }


def cochange_features(changed: list[str], idx: dict) -> dict:
    """cochange_degree + cochange_partners_missing across the changed set."""
    changed_set = set(changed)
    degrees, missing = [], []
    for p in changed:
        partners = idx["cochange"].get(p)
        if not partners:
            degrees.append(0)
            continue
        strong = [q for q, c in partners.items() if c >= 3]
        degrees.append(len(strong))
        miss = sum(1 for q in strong if q not in changed_set)
        missing.append(miss)
    return {
        "cochange_degree": max(degrees) if degrees else 0,
        "cochange_partners_missing": max(missing) if missing else 0,
    }


import math as _math


def _commit_entropy(rows: list[tuple[int, int, str]]) -> float:
    """Normalized change entropy for a single commit's numstat rows (0..1),
    matching diff_stats._entropy so the population is comparable to the PR."""
    per_file = [a + d for a, d, _ in rows]
    total = sum(per_file)
    if total <= 0 or len(per_file) <= 1:
        return 0.0
    ent = 0.0
    for c in per_file:
        if c <= 0:
            continue
        p = c / total
        ent -= p * _math.log2(p)
    return ent / _math.log2(len(per_file))


def experience_features(author_email: str, changed: list[str], idx: dict) -> dict:
    """Kamei EXP / REXP / SEXP for the PR author, as of base.
      EXP  = number of prior commits by the author
      REXP = recency-weighted prior commits (recent count more)
      SEXP = prior commits by the author in the touched subsystems
    """
    a = author_email.lower()
    exp = idx["author_total_authored"].get(a, 0)
    rexp = round(idx["author_rexp"].get(a, 0.0), 4)
    touched_subs = {config.subsystem_of(p) for p in changed}
    subs = idx["author_subsys"].get(a, {})
    sexp = sum(subs.get(s, 0) for s in touched_subs)
    return {"EXP": exp, "REXP": rexp, "SEXP": sexp}


def _pctile_rank(pop: list, x) -> float | None:
    """Fraction of the population strictly less than x (0..1), or None if the
    population is empty or x is unavailable."""
    if not pop or x is None:
        return None
    return round(sum(1 for v in pop if v < x) / len(pop), 4)


def percentile_features(pr_size, pr_nf, pr_entropy, idx: dict) -> dict:
    """Where this PR sits in the repo's own distribution of change size, file
    count, and spread — the 'repo-relative' signals. Population is per-commit
    (a point-in-time proxy for the repo's PR-size distribution)."""
    return {
        "pr_size_pctile_repo": _pctile_rank(idx.get("size_pop", []), pr_size),
        "nf_pctile_repo": _pctile_rank(idx.get("nf_pop", []), pr_nf),
        "entropy_pctile_repo": _pctile_rank(idx.get("entropy_pop", []), pr_entropy),
    }


def test_expectation_features(changed: list[str], per_file: dict, st: config.Settings) -> dict:
    """expected_test_cochange_missing: this PR changes a source file that usually
    ships with a test change (test_cochange_ratio >= 0.5), but the PR itself
    includes no test file — i.e. 'you probably forgot the test'."""
    pr_has_test = any(config.path_flags(f)["is_test"] for f in changed)
    if pr_has_test:
        return {"expected_test_cochange_missing": False}
    missing = False
    for p in changed:
        if config.path_flags(p)["is_test"]:
            continue
        ratio = (per_file.get(p) or {}).get("test_cochange_ratio", 0.0) or 0.0
        if ratio >= 0.5:
            missing = True
            break
    return {"expected_test_cochange_missing": missing}


def is_fix_pr(pr_title: str, head_subject: str, st: config.Settings) -> bool:
    """FIX (2a): is this PR a bug fix, by keyword on its title / head commit."""
    return config.is_fix_text(f"{pr_title or ''} {head_subject or ''}")
