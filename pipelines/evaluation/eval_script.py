#!/usr/bin/env python
"""
eval_script.py - evaluate one Riffle model from a CSV of test PRs and its predictions.

    python pipelines/evaluation/eval_script.py --csv pipelines/evaluation/eval_sample_input.csv --out eval_results/sample

It prints every result, saves the same text to report.txt, and writes tables (CSV) and charts (PNG) to --out.

The CSV has one row per test PR. To include several training seeds, repeat each PR once per seed and add a
seed column.
  needed     repo, pr_number, outcome_3class (lands_clean / lands_after_rework / goes_nowhere),
             p_clean, p_rework, p_nowhere: the model's probabilities for those three outcomes, adding up to 1
  optional   p_reason_abandoned, p_reason_duplicate, p_reason_unwanted, p_reason_ci_technical, p_reason_unknown:
             why a PR goes nowhere (adding up to 1), checked against nowhere_reason
             p_<label> for any other mined label, next to that label (p_label_merged, p_label_bad, ...), and
             p_close_reason_<value> for close_reason
             created_at, closed_at, observed_through: the review-queue simulation, results over time, and
             dropping PRs too recent to have a settled outcome
             author_association, author_is_bot, ai_agent_authored, domain, tier, LA, LD,
             repo_outside_closed_count, repo_median_first_response_hours: results per group
             seed; latency_ms (time to score one PR); and any feature columns, which are checked for leaks
             heuristic_p_clean, heuristic_p_rework, heuristic_p_nowhere: a simple baseline's probabilities (for
             example the outcome mix by contributor type), shown next to the model in the score and the queue

Optional files:
  --train          training data: the usual outcome mix, and how long PRs of each outcome stay open
  --gold           human-checked reasons: repo, pr_number, gold_reason [, gold_outcome, rater_1, rater_2]
  --features-used  the columns the model reads, one per line

Needs numpy, pandas and matplotlib (pip install numpy pandas matplotlib).
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import os
import re
import sys
import warnings
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

try:                                   # required: every run draws its charts
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError:                    # pragma: no cover
    sys.exit("eval_script.py needs matplotlib for its charts. Install it with: pip install matplotlib")

CLASSES = ("lands_clean", "lands_after_rework", "goes_nowhere")
SHORT = ("clean", "rework", "nowhere")
PROB_COLS = ("p_clean", "p_rework", "p_nowhere")
CLEAN, REWORK, NOWHERE = 0, 1, 2
EV_GAIN = np.array([1.0, 0.6, 0.0])    # value of reviewing a PR that lands clean / lands after rework / goes nowhere
REASONS = ("abandoned", "duplicate", "unwanted", "ci_technical", "unknown")
UNKNOWN = 4
REASON_COLS = tuple(f"p_reason_{r}" for r in REASONS)
OUTSIDE = ("CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR", "FIRST_TIMER", "NONE")
FIRST_TIME = ("FIRST_TIME_CONTRIBUTOR", "FIRST_TIMER")
POST_OPEN = ("post_open_commits", "post_open_force_pushes", "changes_requested_reviews")
OUTCOME_COLUMNS = ("outcome_3class", "nowhere_reason", "close_reason", "close_reason_source", "merged_at",
                   "closed_at", "label_bad_observed", "ci_observed", "ci_fail_flaky_only", "revert_window_closed",
                   "hotfix_window_closed", "szz_window_closed", "days_to_hotfix", "days_to_revert", "days_to_szz_fix")
NON_FEATURES = ("repo", "pr_number", "pr_id", "head_sha", "base_sha", "author_email", "created_at",
                "observed_through", "mined_at", "miner_version", "miner_commit", "split", "seed", "latency_ms",
                "gold_reason", "gold_outcome", "rater_1", "rater_2")
MAIN_GROUPS = ("contributor", "author_kind", "tier", "repo_history")   # the model must beat the base rate in each
GROUP_TITLES = {"contributor": "contributor type", "author_kind": "agent or human", "size_quartile": "PR size",
                "domain": "domain", "tier": "repo tier", "responsiveness": "maintainer response speed",
                "repo_history": "earlier closed outside PRs in the repo", "month": "month opened"}
QUEUE_METRICS = ("p_at_5", "p_at_10", "r_at_10", "ndcg_at_5", "ndcg_at_10", "effort_score", "p_at_5_new",
                 "ndcg_at_10_new")
QUEUE_NAMES = {"p_at_5": "nowhere list top-5 hit rate", "p_at_10": "nowhere list top-10 hit rate",
               "r_at_10": "dying PRs caught in top 10", "ndcg_at_5": "review-next top-5 quality",
               "ndcg_at_10": "review-next top-10 quality", "effort_score": "effort-aware score",
               "p_at_5_new": "nowhere top-5, new PRs only", "ndcg_at_10_new": "review-next top-10, new PRs only"}
SCORER_NAMES = {"model": "model", "heuristic": "simple heuristic", "pr_age_only": "PR age only",
                "oldest_first": "oldest first", "newest_first": "newest first", "random_order": "random order"}
HEURISTIC_COLS = tuple(f"heuristic_{c}" for c in PROB_COLS)
BASE_NAMES = {"base_overall": "base rate: overall mix", "base_repo": "base rate: repo history",
              "heuristic": "simple heuristic"}
# Every other label the miner writes; a prediction for one goes in a column named p_<label>.
# The rule says which rows count, because an empty label is never treated as "no": "observed:<col>" keeps rows where
# <col> is true; "window:<col>" keeps every "yes" and a "no" only once that observation window has closed.
BINARY_LABELS = {
    "label_merged": ("merge outcome", None),
    "label_rework": ("merge outcome", None),
    "label_abandoned": ("merge outcome", None),
    "label_duplicate": ("merge outcome", None),
    "label_unwanted": ("merge outcome", None),
    "label_ci_technical": ("merge outcome", None),
    "label_bad": ("after merge", "observed:label_bad_observed"),
    "label_revert": ("after merge", "window:revert_window_closed"),
    "label_hotfix": ("after merge", "window:hotfix_window_closed"),
    "label_szz_bug": ("after merge", "window:szz_window_closed"),
    "label_ci_fail": ("after merge", "observed:ci_observed"),
    "label_ci_fail_optional": ("after merge", "observed:ci_observed"),
}
DEFECT_LABELS = ("label_bad", "label_revert", "label_hotfix", "label_szz_bug", "label_ci_fail", "label_ci_fail_optional")
MULTICLASS_LABELS = {"close_reason": ("duplicate", "unwanted", "ci_technical", "abandoned", "other")}
OBS_COLUMNS = ("label_bad_observed", "ci_observed", "revert_window_closed", "hotfix_window_closed", "szz_window_closed")


@dataclass
class Settings:
    boot: int = 2000            # how many times test repos are resampled for the 95% ranges
    seed: int = 0
    ece_bins: int = 15          # equal-size groups of PRs for the calibration error
    ece_max: float = 0.03
    ece_tier_max: float = 0.05
    slope_lo: float = 0.8
    slope_hi: float = 1.2
    min_coverage: float = 0.99
    sum_tol: float = 1e-4
    latency_p95_ms: float = 200.0
    few_prs: int = 200          # groups smaller than this are shown but not judged
    few_repos: int = 10
    probe_auc: float = 0.85
    shrink_k: float = 30.0      # pulls a repo's own outcome mix toward the overall mix while it has little history
    moments_per_repo: int = 50
    min_open: int = 10
    new_days: float = 14.0      # "new PRs": opened in the last 14 days
    min_new: int = 8
    random_draws: int = 10
    maturity_days: int = 90


# ===================================================================== small helpers
def _nanmean(a, axis=None):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        return np.nanmean(a, axis=axis)


def _pct(samples, alpha=0.05):
    s = np.asarray(samples, float)
    s = s[np.isfinite(s)]
    if s.size == 0:
        return (np.nan, np.nan)
    lo, hi = np.percentile(s, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return float(lo), float(hi)


def _is(series: pd.Series, value) -> np.ndarray:
    return series.eq(value).fillna(False).astype(bool).to_numpy()


def _read(path: str, columns=None) -> pd.DataFrame:
    low = path.lower()
    if low.endswith(".csv"):
        want = None if columns is None else set(columns)
        return pd.read_csv(path, low_memory=False, usecols=(lambda c: c in want) if want else None)
    if low.endswith(".parquet"):
        if columns is not None:
            try:
                import pyarrow.parquet as pq
                have = set(pq.read_schema(path).names)
                columns = [c for c in columns if c in have]
            except ImportError:
                columns = None
        return pd.read_parquet(path, columns=columns)
    raise SystemExit(f"cannot read {path}: use .csv or .parquet")


def _utc_seconds(s: pd.Series) -> np.ndarray:
    if pd.api.types.is_datetime64_any_dtype(s):
        ts = pd.to_datetime(s, utc=True)
    else:
        try:
            ts = pd.to_datetime(s, utc=True, errors="coerce", format="ISO8601")
        except (TypeError, ValueError):
            ts = None
        if ts is None or (ts.isna().all() and s.notna().any()):   # pandas < 2 has no "ISO8601" format
            ts = pd.to_datetime(s, utc=True, errors="coerce")
    return (ts - pd.Timestamp("1970-01-01", tz="UTC")).dt.total_seconds().to_numpy(dtype=float)


def _key_from_repo_pr(repo: pd.Series, pr: pd.Series) -> pd.Series:
    num = pd.to_numeric(pr, errors="coerce").round().astype("Int64").astype(str)
    return repo.astype(str).str.strip().str.lower() + "#" + num


def _keys(df: pd.DataFrame, what: str) -> pd.Series:
    if {"repo", "pr_number"} <= set(df.columns):
        return _key_from_repo_pr(df["repo"], df["pr_number"])
    raise SystemExit(f"{what} needs repo and pr_number columns")


def _ci(v, lo, hi, nd=4):
    if v is None or not np.isfinite(v):
        return "n/a"
    if lo is None or hi is None or not (np.isfinite(lo) and np.isfinite(hi)):
        return f"{v:.{nd}f}"
    return f"{v:.{nd}f} [{lo:.{nd}f}, {hi:.{nd}f}]"


def _table(df: pd.DataFrame, nd=4) -> str:
    if df is None or df.empty:
        return "(nothing to show)"
    with pd.option_context("display.width", 220, "display.max_columns", 60, "display.max_rows", 400,
                           "display.max_colwidth", 80, "display.float_format", ("{:." + str(nd) + "f}").format):
        return df.to_string(index=False)


def _section(title: str):
    print("\n" + "=" * 100 + f"\n{title}\n" + "=" * 100)


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name)


def _n(count: int, word: str) -> str:
    """'1 repo', '4 repos'."""
    return f"{count:,} {word}{'' if count == 1 else 's'}"


def _write_json(path, obj):
    def clean(x):
        if isinstance(x, dict):
            return {str(k): clean(v) for k, v in x.items()}
        if isinstance(x, (list, tuple)):
            return [clean(v) for v in x]
        if isinstance(x, (bool, np.bool_)):
            return bool(x)
        if isinstance(x, (int, np.integer)):
            return int(x)
        if isinstance(x, (float, np.floating)):
            return float(x) if math.isfinite(x) else None
        if isinstance(x, np.ndarray):
            return clean(x.tolist())
        return x
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(clean(obj), fh, indent=2, default=str)


class _Tee:
    """Sends printed text to the console and to report.txt."""
    def __init__(self, path, stream):
        self.file = open(path, "w", encoding="utf-8")
        self.stream = stream

    def write(self, s):
        self.stream.write(s)
        self.file.write(s)

    def flush(self):
        self.stream.flush()
        self.file.flush()

    def close(self):
        self.file.close()


# ===================================================================== metrics
def one_hot(y, k=3):
    out = np.zeros((len(y), k))
    out[np.arange(len(y)), y] = 1.0
    return out


def brier_rows(P, y):
    """Brier score per PR: sum over outcomes of (probability - what happened)^2. 0 is perfect, 2 the worst."""
    return ((P - one_hot(y, P.shape[1])) ** 2).sum(axis=1)


def rps_rows(P, y):
    """Ordered score per PR (ranked probability score): like Brier, but treats the outcomes as ordered
    (nowhere < rework < clean), so mixing up clean and rework costs less than clean and nowhere. 0 to 1."""
    f1 = P[:, NOWHERE]
    f2 = P[:, NOWHERE] + P[:, REWORK]
    o1 = (y == NOWHERE).astype(float)
    o2 = ((y == NOWHERE) | (y == REWORK)).astype(float)
    return ((f1 - o1) ** 2 + (f2 - o2) ** 2) / 2.0


def log_loss_rows(P, y, eps=1e-15):
    return -np.log(np.clip(P[np.arange(len(y)), y], eps, 1.0))


def group_mean(values, codes, n_groups):
    s = np.bincount(codes, weights=values, minlength=n_groups)
    c = np.bincount(codes, minlength=n_groups)
    with np.errstate(invalid="ignore", divide="ignore"):
        return s / c


def _equal_mass_bins(p, bins):
    order = np.argsort(p, kind="stable")
    return [b for b in np.array_split(order, max(1, min(bins, len(p)))) if len(b)]


def ece_binary(yb, p, bins=15):
    """Calibration error: sort PRs by predicted probability into equal-size groups and average the gap between
    the predicted and the actual rate. 0 is perfect."""
    n = len(p)
    if n == 0:
        return np.nan
    return float(sum(len(b) / n * abs(yb[b].mean() - p[b].mean()) for b in _equal_mass_bins(p, bins)))


def debiased_ce_binary(yb, p, bins=15):
    """Calibration error with the extra error that small groups add by chance taken out (Kumar, Liang and Ma, 2019;
    the square root of the corrected squared gap). More trustworthy than the plain one on small test sets."""
    n = len(p)
    if n == 0:
        return np.nan
    tot = 0.0
    for b in _equal_mass_bins(p, bins):
        nb = len(b)
        if nb < 2:
            continue
        ybar, pbar = yb[b].mean(), p[b].mean()
        tot += nb / n * ((ybar - pbar) ** 2 - ybar * (1 - ybar) / (nb - 1))
    return math.sqrt(max(tot, 0.0))


def classwise(fn, P, y, **kw):
    vals = np.array([fn((y == k).astype(float), P[:, k], **kw) for k in range(P.shape[1])])
    return float(_nanmean(vals)), vals


def _logit(p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def _sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -35, 35)))


def _separated(yb, p):
    """True when the predictions split yes from no without a single mistake; then no calibration slope exists."""
    yb, x = np.asarray(yb, float), np.asarray(p, float)
    pos, neg = x[yb == 1], x[yb == 0]
    if pos.size == 0 or neg.size == 0:
        return False
    return bool(pos.min() >= neg.max() or neg.min() >= pos.max())


def _slope_text(v, yb, p):
    if v is not None and np.isfinite(v):
        return f"{v:.4f}"
    return "n/a (perfectly separated)" if _separated(yb, p) else "n/a"


def calibration_offset_slope(yb, p):
    """Fits actual = offset + slope * predicted on the log-odds scale. Ideal: offset 0, slope 1. A slope below 1 means
    the predictions are too confident; an offset below 0 means they run too high on average."""
    yb = np.asarray(yb, float)
    if len(yb) < 2 or yb.min() == yb.max():
        return np.nan, np.nan
    x = _logit(np.asarray(p, float))
    a = 0.0
    for _ in range(100):
        mu = _sigmoid(a + x)
        h = float((mu * (1 - mu)).sum())
        if h <= 0:
            break
        step = float((yb - mu).sum()) / h
        a += step
        if abs(step) < 1e-10:
            break
    if _separated(yb, p):
        return float(a), np.nan          # no finite slope exists, the fit would run away
    X = np.column_stack([np.ones_like(x), x])
    beta = np.array([0.0, 1.0])
    converged = False
    for _ in range(100):
        mu = _sigmoid(X @ beta)
        H = X.T @ (X * (mu * (1 - mu))[:, None])
        try:
            step = np.linalg.solve(H, X.T @ (yb - mu))
        except np.linalg.LinAlgError:
            return float(a), np.nan
        beta = beta + step
        if np.max(np.abs(step)) < 1e-10:
            converged = True
            break
    if not converged or not np.all(np.isfinite(beta)) or abs(beta[1]) > 50:
        return float(a), np.nan
    return float(a), float(beta[1])


def reliability_points(yb, p, bins=15):
    bs = _equal_mass_bins(p, bins)
    return np.array([p[b].mean() for b in bs]), np.array([yb[b].mean() for b in bs])


def auroc_binary(yb, score):
    """Chance that a random PR with the outcome gets a higher score than a random PR without it (ties count half).
    0.5 is a coin flip, 1 is perfect."""
    yb = np.asarray(yb, bool)
    n1 = int(yb.sum())
    n0 = len(yb) - n1
    if n1 == 0 or n0 == 0:
        return np.nan
    ranks = pd.Series(np.asarray(score, float)).rank(method="average").to_numpy()
    return float((ranks[yb].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def _curve_counts(yb, score):
    yb = np.asarray(yb, bool)
    s = np.asarray(score, float)
    order = np.argsort(-s, kind="mergesort")
    s, yb = s[order], yb[order]
    last = np.r_[np.flatnonzero(np.diff(s)), len(s) - 1]       # last PR of each distinct score
    tp = np.cumsum(yb)[last].astype(float)
    return tp, (last + 1) - tp


def average_precision(yb, score):
    """Area under the precision-recall curve: how well the model finds the PRs with the outcome. Compare it with the
    outcome's share of PRs, which is what random guessing gets."""
    yb = np.asarray(yb, bool)
    pos = yb.sum()
    if pos == 0 or pos == len(yb):
        return np.nan
    tp, fp = _curve_counts(yb, score)
    return float(np.sum(np.diff(np.r_[0.0, tp / pos]) * (tp / (tp + fp))))


def roc_points(yb, score):
    tp, fp = _curve_counts(yb, score)
    return np.r_[0.0, fp / fp[-1]], np.r_[0.0, tp / tp[-1]]


def pr_points(yb, score):
    tp, fp = _curve_counts(yb, score)
    return np.r_[0.0, tp / tp[-1]], np.r_[1.0, tp / (tp + fp)]


def confusion(y_true, y_pred, k):
    return np.bincount(np.asarray(y_true) * k + np.asarray(y_pred), minlength=k * k).reshape(k, k)


def prf_from_confusion(cm, labels):
    out = {}
    for i in labels:
        tp = cm[i, i]
        fp = cm[:, i].sum() - tp
        fn = cm[i, :].sum() - tp
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        out[i] = (float(prec), float(rec), float(2 * prec * rec / (prec + rec)) if prec + rec else 0.0,
                  int(cm[i, :].sum()))
    return out


def macro_f1_cm(cm, labels):
    prf = prf_from_confusion(cm, labels)
    return float(np.mean([prf[i][2] for i in labels]))


def balanced_accuracy_cm(cm):
    rows = cm.sum(axis=1)
    rec = [cm[i, i] / rows[i] for i in range(len(cm)) if rows[i] > 0]
    return float(np.mean(rec)) if rec else np.nan


def cohen_kappa(a, b, k):
    """Agreement between two sets of labels beyond what chance would give: 1 is full agreement, 0 is chance."""
    cm = confusion(a, b, k).astype(float)
    n = cm.sum()
    if n == 0:
        return np.nan
    po = np.trace(cm) / n
    pe = float((cm.sum(axis=0) * cm.sum(axis=1)).sum()) / n ** 2
    return float((po - pe) / (1 - pe)) if pe < 1 else np.nan


def ndcg_at_k(gain_ranked, gain_all, k):
    """List quality: how close the top k of the list is to the best possible order (1 = best)."""
    disc = 1.0 / np.log2(np.arange(2, k + 2))
    g = gain_ranked[:k]
    ideal = np.sort(gain_all)[::-1][:k]
    idcg = float((ideal * disc[:len(ideal)]).sum())
    return float((g * disc[:len(g)]).sum()) / idcg if idcg > 0 else np.nan


def _area(effort_sorted, hits_sorted, total_effort, total_hits):
    e = np.concatenate([[0.0], np.cumsum(effort_sorted)]) / total_effort
    r = np.concatenate([[0.0], np.cumsum(hits_sorted)]) / total_hits
    return float(np.sum((e[1:] - e[:-1]) * (r[1:] + r[:-1]) / 2.0))


def effort_score(yb, score, effort):
    """Effort-aware score: how many target PRs are found per line of code read, when going down the list.
    1 is the best possible order, 0 the worst."""
    yb = np.asarray(yb, float)
    pos = yb.sum()
    if pos == 0 or pos == len(yb):
        return np.nan
    te = float(effort.sum())
    order = np.argsort(-np.asarray(score, float), kind="stable")
    best = np.lexsort((effort, -yb))
    m = _area(effort[order], yb[order], te, pos)
    o = _area(effort[best], yb[best], te, pos)
    w = _area(effort[best[::-1]], yb[best[::-1]], te, pos)
    return 1.0 - (o - m) / (o - w) if o > w else np.nan


def recall_at_effort(yb, score, effort, share=0.2):
    """Share of target PRs caught when reviewing the highest-scored PRs until `share` of all changed lines is read."""
    pos = yb.sum()
    if pos == 0:
        return np.nan
    order = np.argsort(-np.asarray(score, float), kind="stable")
    take = np.cumsum(effort[order]) / effort.sum() <= share
    return float(yb[order][take].sum() / pos)


def boot_index(n_units, B, seed):
    return np.random.default_rng(seed).integers(0, n_units, size=(B, n_units))


def boot_mean_ci(per_unit, idx, alpha=0.05):
    v = np.asarray(per_unit, float)
    return _pct(_nanmean(v[idx], axis=1), alpha)


def boot_skill(per_unit_model, per_unit_ref, idx, alpha=0.05):
    """Skill = 1 - model score / base-rate score, averaged over repos, with a 95% range from resampling repos."""
    m = np.asarray(per_unit_model, float)
    r = np.asarray(per_unit_ref, float)
    ok = np.isfinite(m) & np.isfinite(r)
    m, r = np.where(ok, m, np.nan), np.where(ok, r, np.nan)
    est = 1.0 - _nanmean(m) / _nanmean(r)
    with np.errstate(invalid="ignore", divide="ignore"):
        samples = 1.0 - _nanmean(m[idx], axis=1) / _nanmean(r[idx], axis=1)
    lo, hi = _pct(samples, alpha)
    return float(est), lo, hi


def boot_diff_ci(a, b, idx, alpha=0.05):
    """Mean per-repo difference a - b with a 95% range from resampling repos."""
    d = np.asarray(a, float) - np.asarray(b, float)
    ok = np.isfinite(d)
    if ok.sum() < 2:
        return np.nan, np.nan, np.nan
    lo, hi = _pct(_nanmean(np.where(ok, d, np.nan)[idx], axis=1), alpha)
    return float(d[ok].mean()), lo, hi


def boot_macro_f1(y_t, y_p, unit, labels, k, B, seed, alpha=0.05):
    if len(y_t) == 0:
        return (np.nan, np.nan)
    units, inv = np.unique(unit, return_inverse=True)
    cms = np.zeros((len(units), k, k))
    np.add.at(cms, (inv, y_t, y_p), 1)
    idx = np.random.default_rng(seed).integers(0, len(units), size=(B, len(units)))
    return _pct(np.array([macro_f1_cm(cms[i].sum(axis=0).astype(int), labels) for i in idx]), alpha)


# ===================================================================== loading
def _population(df: pd.DataFrame, all_authors: bool, notes: list | None = None) -> pd.DataFrame:
    n0 = len(df)
    df = df[df["outcome_3class"].isin(CLASSES)]
    if notes is not None and n0 - len(df):
        notes.append(f"{n0 - len(df):,} PRs without an outcome_3class answer left out")
    if not all_authors:
        if "author_association" in df.columns:
            n1 = len(df)
            df = df[df["author_association"].isin(OUTSIDE)]
            if notes is not None and n1 - len(df):
                notes.append(f"{n1 - len(df):,} PRs by maintainers or members left out (outside contributors only)")
        if "author_is_bot" in df.columns:
            n2 = len(df)
            df = df[~_is(df["author_is_bot"], True)]
            if notes is not None and n2 - len(df):
                notes.append(f"{n2 - len(df):,} bot PRs left out")
    return df


def load_csv(args, S: Settings, notes: list) -> dict:
    raw = _read(args.csv)
    need = ["repo", "pr_number", "outcome_3class", *PROB_COLS]
    miss = [c for c in need if c not in raw.columns]
    if miss:
        raise SystemExit(f"--csv is missing column(s): {', '.join(miss)}")
    raw = raw.copy()
    raw["_key"] = _keys(raw, "--csv")
    seeds = [None]
    if "seed" in raw.columns and raw["seed"].nunique(dropna=True) > 1:
        seeds = sorted(raw["seed"].dropna().unique().tolist())
    raw = raw.sort_values(["_key"] + (["seed"] if "seed" in raw.columns else []), kind="stable")
    conflicts = int(raw.groupby("_key")["outcome_3class"].nunique(dropna=False).gt(1).sum())
    df = _population(raw.drop_duplicates("_key"), args.all_authors, notes).copy()
    df["_created_s"] = _utc_seconds(df["created_at"]) if "created_at" in df.columns else np.nan
    df["_closed_s"] = _utc_seconds(df["closed_at"]) if "closed_at" in df.columns else np.nan
    if "observed_through" in df.columns and np.isfinite(df["_created_s"]).any():
        young = ((_utc_seconds(df["observed_through"]) - df["_created_s"]) < S.maturity_days * 86400).to_numpy()
        if young.any():
            notes.append(f"{int(young.sum()):,} PRs opened less than {S.maturity_days} days before the data was "
                         "collected left out (their outcome may not be settled yet)")
            df = df[~young]
    if df.empty:
        raise SystemExit("no PRs left to evaluate")
    df["y"] = df["outcome_3class"].map({c: i for i, c in enumerate(CLASSES)}).astype(int)
    df["_repo"] = df["repo"].astype(str).str.strip().str.lower()
    df = df.sort_values(["_repo", "_key"]).reset_index(drop=True)
    df["_code"] = pd.Categorical(df["_repo"], categories=sorted(df["_repo"].unique())).codes.astype(int)
    return dict(df=df, raw=raw, seeds=seeds, label_conflicts=conflicts)


def outcome_mix(args, df, notes):
    """The usual outcome mix: from --train when given, otherwise from the test set itself."""
    if args.train:
        t = _read(args.train, columns=["repo", "pr_number", "outcome_3class", "author_association", "author_is_bot",
                                       "head_sha", "created_at", "closed_at", *BINARY_LABELS, *OBS_COLUMNS])
        if not {"repo", "pr_number", "outcome_3class"} <= set(t.columns):
            raise SystemExit("--train needs repo, pr_number and outcome_3class columns")
        t = _population(t, args.all_authors)
        counts = t["outcome_3class"].value_counts()
        mix = np.array([counts.get(c, 0) for c in CLASSES], float)
        if mix.sum() > 0:
            notes.append(f"usual outcome mix taken from --train ({int(mix.sum()):,} PRs)")
            return mix / mix.sum(), t
    counts = np.bincount(df["y"], minlength=3).astype(float)
    notes.append("usual outcome mix taken from the test set itself (pass --train to use the training data)")
    return counts / counts.sum(), None


def overlap_check(df, train):
    if train is None:
        return None
    tk = set(_key_from_repo_pr(train["repo"], train["pr_number"]))
    out = {"same PRs": int(df["_key"].isin(tk).sum()),
           "same repos": len(set(df["_repo"]) & set(train["repo"].astype(str).str.strip().str.lower()))}
    if "head_sha" in df.columns and "head_sha" in train.columns:
        out["same head commits"] = int(df["head_sha"].isin(set(train["head_sha"].dropna())).sum())
    return out


def extract_predictions(raw, df, seeds, mix, S: Settings, label_conflicts=0) -> dict:
    """Lines the predictions up with the test PRs and checks them. Several seeds are averaged."""
    keys = df["_key"]
    has_reason = all(c in raw.columns for c in REASON_COLS)
    chk = dict(seeds=len(seeds), duplicates=0, blank_rows=0, outside_0_1=0, not_adding_to_1=0, coverage=1.0,
               reason_rows_not_adding_to_1=0, answer_differs_between_seeds=int(label_conflicts),
               partial_reason_columns=any(c in raw.columns for c in REASON_COLS) and not has_reason)
    P_seeds, R_seeds = [], []
    for s in seeds:
        sub = raw if s is None else raw[raw["seed"] == s]
        chk["duplicates"] += int(sub["_key"].duplicated().sum())
        sub = sub.drop_duplicates("_key").set_index("_key")
        al = sub.reindex(keys.to_numpy())
        present = keys.isin(sub.index).to_numpy()
        P = al[list(PROB_COLS)].apply(pd.to_numeric, errors="coerce").to_numpy(float)
        blank = present & np.isnan(P).any(axis=1)
        chk["blank_rows"] += int(blank.sum())
        good = present & ~blank
        chk["outside_0_1"] += int(((P[good] < -1e-9) | (P[good] > 1 + 1e-9)).any(axis=1).sum())
        chk["not_adding_to_1"] += int((np.abs(P[good].sum(axis=1) - 1.0) > S.sum_tol).sum())
        chk["coverage"] = min(chk["coverage"], float(good.mean()))
        P = np.where(good[:, None], np.clip(np.nan_to_num(P), 0, 1), mix[None, :])
        P_seeds.append(P / P.sum(axis=1, keepdims=True))
        if has_reason:
            Rr = al[list(REASON_COLS)].apply(pd.to_numeric, errors="coerce").to_numpy(float)
            rok = ~np.isnan(Rr).any(axis=1)
            chk["reason_rows_not_adding_to_1"] += int((np.abs(Rr[rok].sum(axis=1) - 1.0) > S.sum_tol).sum())
            Rr = np.where(rok[:, None], np.clip(np.nan_to_num(Rr), 0, 1), np.nan)
            with np.errstate(invalid="ignore", divide="ignore"):
                R_seeds.append(Rr / Rr.sum(axis=1, keepdims=True))
    P = np.mean(P_seeds, axis=0)
    lat = None
    if "latency_ms" in raw.columns:
        v = pd.to_numeric(raw["latency_ms"], errors="coerce").dropna().to_numpy(float)
        if v.size:
            lat = {f"p{q}": float(np.percentile(v, q)) for q in (50, 95, 99)}
    ok = (chk["duplicates"] == 0 and chk["blank_rows"] == 0 and chk["outside_0_1"] == 0 and chk["not_adding_to_1"] == 0
          and chk["coverage"] >= S.min_coverage and chk["reason_rows_not_adding_to_1"] == 0
          and chk["answer_differs_between_seeds"] == 0)
    return dict(P=P / P.sum(axis=1, keepdims=True), R=np.mean(R_seeds, axis=0) if has_reason else None, seeds=seeds,
                P_seeds=P_seeds if len(seeds) > 1 else [], checks=chk, file_ok=ok, latency=lat)


def repo_history_rate(df, mix, k):
    """Each PR gets the outcome mix of the earlier PRs of its repo that had closed before it opened (only what was
    known then), pulled toward the overall mix while the repo has little history."""
    created, closed, y = df["_created_s"].to_numpy(float), df["_closed_s"].to_numpy(float), df["y"].to_numpy()
    P = np.tile(mix, (len(df), 1)).astype(float)
    for _, idx in df.groupby("_repo", sort=True).indices.items():
        c = np.where(np.isfinite(created[idx]), created[idx], -np.inf)
        cl, yy = closed[idx], y[idx]
        counts = np.zeros((len(idx), 3))
        for cls in range(3):
            counts[:, cls] = np.searchsorted(np.sort(cl[(yy == cls) & np.isfinite(cl)]), c, side="left")
        P[idx] = (counts + k * mix) / (counts.sum(axis=1, keepdims=True) + k)
    return P


# ===================================================================== scoring
def score_probs(P, y, codes, n_repos, S: Settings) -> dict:
    br, rp, ll = brier_rows(P, y), rps_rows(P, y), log_loss_rows(P, y)
    out = dict(P=P, brier=br, repo_brier=group_mean(br, codes, n_repos), repo_rps=group_mean(rp, codes, n_repos))
    m = dict(brier=float(_nanmean(out["repo_brier"])), brier_all_prs=float(br.mean()),
             rps=float(_nanmean(out["repo_rps"])), log_loss=float(ll.mean()))
    m["calibration_error"], ece_k = classwise(ece_binary, P, y, bins=S.ece_bins)
    m["calibration_error_corrected"], dce_k = classwise(debiased_ce_binary, P, y, bins=S.ece_bins)
    for k, s in enumerate(SHORT):
        yb = y == k
        m[f"calibration_error_{s}"] = float(ece_k[k])
        m[f"calibration_error_corrected_{s}"] = float(dce_k[k])
        m[f"offset_{s}"], m[f"slope_{s}"] = calibration_offset_slope(yb.astype(float), P[:, k])
        m[f"auroc_{s}"], m[f"auprc_{s}"] = auroc_binary(yb, P[:, k]), average_precision(yb, P[:, k])
    m["auroc_average"] = float(_nanmean([m[f"auroc_{s}"] for s in SHORT]))
    cm = confusion(y, P.argmax(axis=1), 3)
    m["macro_f1"] = macro_f1_cm(cm, range(3))
    m["balanced_accuracy"] = balanced_accuracy_cm(cm)
    m["accuracy"] = float(np.trace(cm) / cm.sum())
    out["metrics"], out["confusion"] = m, cm
    return out


def survival_curves(created, closed, y):
    """Per outcome, how long its PRs stayed open (seconds); PRs never closed count as still open."""
    d = np.asarray(closed, float) - np.asarray(created, float)
    d = np.where(np.isfinite(d), d, np.inf)
    ok = np.isfinite(np.asarray(created, float))
    return [np.sort(d[ok & (np.asarray(y) == k)]) for k in range(3)]


def survival_at(curves, ages):
    S = np.ones((len(ages), 3))
    for k, dk in enumerate(curves):
        n = len(dk)
        if n:
            S[:, k] = np.maximum((n - np.searchsorted(dk, ages, side="right")) / n, 0.5 / n)
    return S


def _age_adjust(P, Sa):
    """A PR that is still open after a while is more likely to be going nowhere: updates the probabilities for the
    PR's age using how long PRs of each outcome usually stay open."""
    if Sa is None:
        return P
    A = P * Sa
    return A / A.sum(axis=1, keepdims=True)


def _queue_row(ev, nw, gains, nowhere_b, effort, new, min_new):
    o = np.argsort(-nw, kind="stable")
    tot = nowhere_b.sum()
    oe = np.argsort(-ev, kind="stable")
    p5n = n10n = np.nan
    if new.sum() >= min_new:
        fo = np.argsort(-nw[new], kind="stable")
        p5n = float(nowhere_b[new][fo[:5]].mean())
        foe = np.argsort(-ev[new], kind="stable")
        n10n = ndcg_at_k(gains[new][foe], gains[new], 10)
    return (float(nowhere_b[o[:5]].mean()), float(nowhere_b[o[:10]].mean()),
            float(nowhere_b[o[:10]].sum() / tot) if tot > 0 else np.nan,
            ndcg_at_k(gains[oe], gains, 5), ndcg_at_k(gains[oe], gains, 10),
            effort_score(nowhere_b, nw, effort) if effort is not None else np.nan, p5n, n10n)


def simulate_queues(df, P, P_seeds, mix, S: Settings, curves, effort, heuristic=None):
    """Replays each test repo's history: at random moments, ranks the PRs that were open then into a 'review next' list
    (by expected value) and a 'likely to go nowhere' list, and checks them against what really happened."""
    created, closed = df["_created_s"].to_numpy(float), df["_closed_s"].to_numpy(float)
    if not (np.isfinite(created).any() and np.isfinite(closed).any()):
        return None
    n = len(df)
    y = df["y"].to_numpy()
    gains, nowhere_b, landed_all = EV_GAIN[y], (y == NOWHERE).astype(float), y != NOWHERE
    scorers = {"model": P}
    if heuristic is not None:
        scorers["heuristic"] = heuristic
    if curves is not None:
        scorers["pr_age_only"] = np.tile(mix, (n, 1))
    rng = np.random.default_rng(S.seed + 7)
    recs, jacc = [], []
    present, flagged = np.zeros(n), np.zeros(n)
    for repo, idx in df.groupby("_repo", sort=True).indices.items():
        c, cl = created[idx], closed[idx]
        ok = np.isfinite(c)
        if ok.sum() < S.min_open:
            continue
        end = np.where(np.isfinite(cl), cl, np.inf)
        lo, hi = np.nanmin(c), np.nanmax(np.where(np.isfinite(cl), cl, c))
        if not hi > lo:
            continue
        for t in np.sort(rng.uniform(lo, hi, size=S.moments_per_repo)):
            q = idx[ok & (c <= t) & (t < end)]
            if len(q) < S.min_open:
                continue
            g, nb = gains[q], nowhere_b[q]
            eff = effort[q] if effort is not None else None
            age = t - created[q]
            new = age <= S.new_days * 86400
            Sa = survival_at(curves, age) if curves is not None else None
            head = (repo, t, len(q), int(nb.sum()))
            for name, Pm in scorers.items():
                A = _age_adjust(Pm[q], Sa)
                nw = A[:, NOWHERE]
                recs.append(head + (name,) + _queue_row(A @ EV_GAIN, nw, g, nb, eff, new, S.min_new))
                if name == "model":
                    top5 = q[np.argsort(-nw, kind="stable")[:5]]
                    present[q[landed_all[q]]] += 1
                    flagged[top5[landed_all[top5]]] += 1
            if len(P_seeds) > 1:
                tops = [set(q[np.argsort(-_age_adjust(Ps[q], Sa)[:, NOWHERE], kind="stable")[:10]].tolist())
                        for Ps in P_seeds]
                jacc.append(np.mean([len(a & b) / len(a | b) for a, b in itertools.combinations(tops, 2)]))
            recs.append(head + ("oldest_first",) + _queue_row(age, age, g, nb, eff, new, S.min_new))
            recs.append(head + ("newest_first",) + _queue_row(-age, -age, g, nb, eff, new, S.min_new))
            draws = [_queue_row(r, r, g, nb, eff, new, S.min_new) for r in rng.random((S.random_draws, len(q)))]
            recs.append(head + ("random_order",) + tuple(_nanmean(np.array(draws, float), axis=0)))
    if not recs:
        return None
    res = pd.DataFrame(recs, columns=["repo", "t", "open_prs", "open_prs_going_nowhere", "scorer", *QUEUE_METRICS])
    return dict(moments=res, present=present, flagged=flagged, age_adjusted=curves is not None,
                seed_overlap=float(np.mean(jacc)) if jacc else np.nan)


def build_groups(df) -> dict:
    out = {}
    if "author_association" in df.columns:
        a = df["author_association"].astype(object)
        out["contributor"] = np.select([a.isin(FIRST_TIME).to_numpy(), _is(a, "CONTRIBUTOR"), _is(a, "NONE")],
                                       ["first_time", "repeat", "none"], default="other")
    if "ai_agent_authored" in df.columns:
        v = df["ai_agent_authored"].map(lambda x: str(x).strip().lower())
        out["author_kind"] = np.where(v.isin(["true", "1"]), "agent", np.where(v.isin(["false", "0"]), "human", "unknown"))
    if {"LA", "LD"} <= set(df.columns):
        size = pd.to_numeric(df["LA"], errors="coerce") + pd.to_numeric(df["LD"], errors="coerce")
        if size.notna().sum() >= 8:
            q = pd.qcut(size.rank(method="first"), 4, labels=["smallest 25%", "25-50%", "50-75%", "largest 25%"])
            out["size_quartile"] = q.astype(object).where(q.notna(), "unknown").astype(str).to_numpy()
    if "domain" in df.columns:
        out["domain"] = df["domain"].astype(object).where(df["domain"].notna(), "(none)").astype(str).to_numpy()
    if "tier" in df.columns:
        t = df["tier"].astype(object)
        out["tier"] = t.where(t.notna(), "unknown").astype(str).to_numpy()
    if "repo_median_first_response_hours" in df.columns:
        h = pd.to_numeric(df["repo_median_first_response_hours"], errors="coerce")
        if h.notna().sum() >= 30:
            q = pd.qcut(h.rank(method="first"), 3, labels=["fast", "medium", "slow"])
            out["responsiveness"] = q.astype(object).where(q.notna(), "unknown").astype(str).to_numpy()
    if "repo_outside_closed_count" in df.columns:
        n = pd.to_numeric(df["repo_outside_closed_count"], errors="coerce")
        if n.notna().any():
            b = pd.cut(n, [-np.inf, 10, 50, 200, np.inf], labels=["0-10", "11-50", "51-200", "over 200"])
            out["repo_history"] = b.astype(object).where(b.notna(), "unknown").astype(str).to_numpy()
    if np.isfinite(df["_created_s"]).any():
        ts = pd.to_datetime(pd.Series(df["_created_s"].to_numpy()), unit="s", utc=True, errors="coerce")
        out["month"] = ts.dt.strftime("%Y-%m").fillna("unknown").to_numpy()
    return out


def group_table(df, groups, P, brier, ref_brier, queues, S: Settings) -> pd.DataFrame:
    y, codes = df["y"].to_numpy(), df["_code"].to_numpy()
    R = int(codes.max()) + 1
    landed = y != NOWHERE
    recs = []
    for gname, lab in groups.items():
        for g in sorted(pd.unique(lab)):
            m = lab == g
            n, nrep = int(m.sum()), int(np.unique(codes[m]).size)
            b = float(_nanmean(group_mean(brier[m], codes[m], R)))
            rb = float(_nanmean(group_mean(ref_brier[m], codes[m], R)))
            ev = int((y[m] == NOWHERE).sum())
            rec = dict(grouping=gname, group=str(g), prs=n, repos=nrep, few_prs=n < S.few_prs or nrep < S.few_repos,
                       brier=b, skill=(1 - b / rb) if rb > 0 else np.nan,
                       calibration_error=classwise(ece_binary, P[m], y[m], bins=S.ece_bins)[0] if n >= 30 else np.nan,
                       nowhere_offset=(calibration_offset_slope((y[m] == NOWHERE).astype(float), P[m, NOWHERE])[0]
                                       if ev >= 100 and n - ev >= 100 else np.nan),
                       false_flag_rate=np.nan)
            if queues is not None:
                pres = queues["present"][m & landed].sum()
                rec["false_flag_rate"] = float(queues["flagged"][m & landed].sum() / pres) if pres else np.nan
            recs.append(rec)
    return pd.DataFrame(recs)


def why_head(df, pred, gold, S: Settings) -> dict | None:
    rmap = {r: i for i, r in enumerate(REASONS)}
    y_out = df["y"].to_numpy()
    codes = df["_code"].to_numpy()
    agreement = {}
    if gold is not None:
        if "gold_reason" not in gold.columns:
            raise SystemExit("--gold needs a gold_reason column")
        g = gold.assign(_key=_keys(gold, "--gold")).drop_duplicates("_key").set_index("_key")
        al = g.reindex(df["_key"].to_numpy())
        in_set = df["_key"].isin(g.index).to_numpy()
        tr = al["gold_reason"].map(rmap).to_numpy(float)
        y_true = y_out.copy()
        if "gold_outcome" in g.columns:
            go = al["gold_outcome"].map({c: i for i, c in enumerate(CLASSES)}).to_numpy(float)
            y_true = np.where(np.isfinite(go), go, y_out).astype(int)
        source = f"human-checked reasons (--gold, {int(in_set.sum())} PRs in the test set)"
        if {"rater_1", "rater_2"} <= set(g.columns):
            r1 = al["rater_1"].map(rmap).to_numpy(float)
            r2 = al["rater_2"].map(rmap).to_numpy(float)
            both = in_set & np.isfinite(r1) & np.isfinite(r2)
            if both.sum():
                agreement["two people checking the same PRs"] = cohen_kappa(r1[both].astype(int), r2[both].astype(int), 5)
        if "nowhere_reason" in df.columns:
            ds = df["nowhere_reason"].map(rmap).to_numpy(float)
            both = in_set & np.isfinite(ds) & np.isfinite(tr)
            if both.sum():
                agreement["mined reasons vs human-checked"] = cohen_kappa(ds[both].astype(int), tr[both].astype(int), 5)
    elif "nowhere_reason" in df.columns:
        tr = df["nowhere_reason"].map(rmap).to_numpy(float)
        in_set, y_true = np.ones(len(df), bool), y_out
        source = "mined reasons (nowhere_reason; made by rules or an LLM, so somewhat noisy)"
    else:
        return None
    out = dict(source=source, summary=None, per_reason=pd.DataFrame(), confusion=None, agreement=agreement)
    R = pred["R"]
    if R is None:
        return out
    rows = in_set & (y_true == NOWHERE) & np.isfinite(tr) & np.isfinite(R).all(axis=1)
    if rows.sum() == 0:
        return out
    yr, Rr, cr = tr[rows].astype(int), R[rows], codes[rows]
    p5, p4 = Rr.argmax(axis=1), Rr[:, :4].argmax(axis=1)
    ex = yr != UNKNOWN
    cm4, cm5 = confusion(yr[ex], p4[ex], 5), confusion(yr[ex], p5[ex], 5)
    lo, hi = boot_macro_f1(yr[ex], p4[ex], cr[ex], range(4), 5, S.boot, S.seed)
    P = pred["P"]
    jr = in_set & np.isfinite(R).all(axis=1) & ((y_true != NOWHERE) | np.isfinite(tr))
    jt = np.where(y_true[jr] == NOWHERE, 2 + np.nan_to_num(tr[jr]).astype(int), y_true[jr]).astype(int)
    PJ = np.column_stack([P[jr, CLEAN], P[jr, REWORK], P[jr, NOWHERE][:, None] * R[jr]])
    out["summary"] = dict(prs=int(rows.sum()), prs_with_a_known_reason=int(ex.sum()),
                          f1_average=macro_f1_cm(cm4, range(4)), f1_average_lo=lo, f1_average_hi=hi,
                          f1_average_unknown_counts_as_wrong=macro_f1_cm(cm5, range(4)),
                          model_says_unknown=float((p5 == UNKNOWN).mean()), really_unknown=float((yr == UNKNOWN).mean()),
                          outcome_and_reason_brier=float(brier_rows(PJ, jt).mean()) if len(jt) else np.nan)
    out["per_reason"] = pd.DataFrame([dict(reason=REASONS[i], precision=p, recall=r, f1=f, prs=s)
                                      for i, (p, r, f, s) in prf_from_confusion(cm4, range(4)).items()])
    out["confusion"] = confusion(yr, p5, 5)
    return out


# ===================================================================== every other label
def _as_bool(s: pd.Series) -> np.ndarray:
    """1.0 / 0.0 / NaN from booleans, 0/1 or True/False text (a CSV round trip turns booleans into text)."""
    def conv(v):
        if v is None:
            return np.nan
        if isinstance(v, (bool, np.bool_)):
            return float(v)
        if isinstance(v, (int, float, np.integer, np.floating)):
            return float(v != 0) if np.isfinite(v) else np.nan
        t = str(v).strip().lower()
        return 1.0 if t in ("true", "t", "1", "yes") else (0.0 if t in ("false", "f", "0", "no") else np.nan)
    return s.map(conv).to_numpy(float)


def _label_rows(frame, label, rule):
    y = _as_bool(frame[label])
    ok = np.isfinite(y)
    if rule:
        kind, col = rule.split(":", 1)
        if col in frame.columns:
            c = _as_bool(frame[col])
            ok &= (c == 1) if kind == "observed" else ((y == 1) | (c == 1))
    return y, ok


def _effort(df):
    if {"LA", "LD"} <= set(df.columns):
        e = (pd.to_numeric(df["LA"], errors="coerce").to_numpy(float)
             + pd.to_numeric(df["LD"], errors="coerce").to_numpy(float))
        if np.isfinite(e).any():
            return np.where(np.isfinite(e), e, np.nanmedian(e)) + 1.0
    return None


LABEL_FIELDS = ("group", "label", "prs", "repos", "coverage", "share_yes", "base_rate", "base_rate_from", "brier",
                "brier_lo", "brier_hi", "skill", "skill_lo", "skill_hi", "log_loss", "auroc", "auprc",
                "calibration_error", "slope", "offset", "caught_at_20pct_effort", "effort_score", "outside_0_1", "note")


def score_other_labels(df, raw, pred, train, idx, S: Settings, notes, effort) -> dict:
    """Scores every p_<label> column against its label, on the rows where that label counts."""
    out = dict(table=pd.DataFrame(), multiclass=pd.DataFrame(), consistency=pd.DataFrame(), curves={})
    main = set(PROB_COLS) | set(REASON_COLS)
    pcols = [c for c in raw.columns if c.startswith("p_") and c not in main]
    if not pcols:
        return out
    codes = df["_code"].to_numpy()
    R = int(codes.max()) + 1
    num = raw[["_key"] + pcols].copy()
    num[pcols] = num[pcols].apply(pd.to_numeric, errors="coerce")
    pr = num.groupby("_key")[pcols].mean().reindex(df["_key"].to_numpy())     # several seeds are averaged
    known = {f"p_{lab}" for lab in BINARY_LABELS} | {f"p_{lab}_{v}" for lab, vs in MULTICLASS_LABELS.items() for v in vs}
    unused = [c for c in pcols if c not in known]
    if unused:
        notes.append("prediction columns with no matching label, ignored: " + ", ".join(unused))
    rows = []
    for label, (group, rule) in BINARY_LABELS.items():
        pc = f"p_{label}"
        if pc not in pr.columns:
            continue
        rec = dict.fromkeys(LABEL_FIELDS, np.nan)
        rec.update(group=group, label=label, note="")
        if label not in df.columns:
            rec["note"] = f"the CSV has no {label} column"
            rows.append(rec)
            continue
        y, ok = _label_rows(df, label, rule)
        p = pr[pc].to_numpy(float)
        m = ok & np.isfinite(p)
        rec["prs"] = int(m.sum())
        rec["coverage"] = float(np.isfinite(p[ok]).mean()) if ok.any() else np.nan
        if m.sum() < 20 or np.unique(y[m]).size < 2:
            rec["note"] = "too few PRs, or only one answer"
            rows.append(rec)
            continue
        yb, cc = y[m], codes[m]
        rec["outside_0_1"] = int(((p[m] < -1e-9) | (p[m] > 1 + 1e-9)).sum())
        pp = np.clip(p[m], 0, 1)
        base, src = float(yb.mean()), "test set"
        if train is not None and label in train.columns:
            ty, tok = _label_rows(train, label, rule)
            if tok.sum() >= 30:
                base, src = float(ty[tok].mean()), "train"
        rep = group_mean((pp - yb) ** 2, cc, R)
        rep_base = group_mean((base - yb) ** 2, cc, R)
        lo, hi = boot_mean_ci(rep, idx)
        sk, slo, shi = boot_skill(rep, rep_base, idx)
        a, s = calibration_offset_slope(yb, pp)
        pcl = np.clip(pp, 1e-15, 1 - 1e-15)
        rec.update(repos=int(np.unique(cc).size), share_yes=float(yb.mean()), base_rate=base, base_rate_from=src,
                   brier=float(_nanmean(rep)), brier_lo=lo, brier_hi=hi, skill=sk, skill_lo=slo, skill_hi=shi,
                   log_loss=float(-np.mean(yb * np.log(pcl) + (1 - yb) * np.log(1 - pcl))),
                   auroc=auroc_binary(yb == 1, pp), auprc=average_precision(yb == 1, pp),
                   calibration_error=ece_binary(yb, pp, S.ece_bins), slope=s, offset=a)
        if label in DEFECT_LABELS and effort is not None:
            rec["caught_at_20pct_effort"] = recall_at_effort(yb, pp, effort[m])
            rec["effort_score"] = effort_score(yb, pp, effort[m])
        rows.append(rec)
        out["curves"][label] = (yb, pp)
    if rows:
        out["table"] = pd.DataFrame(rows, columns=list(LABEL_FIELDS))
    mc = []
    for label, values in MULTICLASS_LABELS.items():
        cols = [f"p_{label}_{v}" for v in values]
        if label not in df.columns or not all(c in pr.columns for c in cols):
            continue
        yt = df[label].map({v: i for i, v in enumerate(values)}).to_numpy(float)
        Pm = pr[cols].to_numpy(float)
        m = np.isfinite(yt) & np.isfinite(Pm).all(axis=1)
        if m.sum() < 20:
            continue
        Pm = np.clip(Pm[m], 0, 1)
        Pm = Pm / np.maximum(Pm.sum(axis=1, keepdims=True), 1e-12)
        yi = yt[m].astype(int)
        cm = confusion(yi, Pm.argmax(axis=1), len(values))
        mc.append(dict(label=label, answers="/".join(values), prs=int(m.sum()), brier=float(brier_rows(Pm, yi).mean()),
                       log_loss=float(log_loss_rows(Pm, yi).mean()), f1_average=macro_f1_cm(cm, range(len(values))),
                       accuracy=float(np.trace(cm) / cm.sum())))
    out["multiclass"] = pd.DataFrame(mc)
    cons, P, Rr = [], pred["P"], pred["R"]

    def gap(col, should_match, other):
        if col not in pr.columns:
            return
        a = pr[col].to_numpy(float)
        ok = np.isfinite(a) & np.isfinite(other)
        if ok.sum():
            d = np.abs(a[ok] - other[ok])
            cons.append(dict(prediction=col, should_match=should_match, average_gap=float(d.mean()),
                             largest_gap=float(d.max()), result="CHECK" if d.mean() > 0.05 else "OK"))

    gap("p_label_merged", "p_clean + p_rework", P[:, CLEAN] + P[:, REWORK])
    if Rr is not None:
        for i, r in enumerate(REASONS[:4]):
            gap(f"p_label_{r}", f"p_nowhere x p_reason_{r}", P[:, NOWHERE] * Rr[:, i])
    out["consistency"] = pd.DataFrame(cons)
    return out


# ===================================================================== leak checks
def banned_columns(data_cols):
    banned = set(POST_OPEN) | set(OUTCOME_COLUMNS) | {c for c in data_cols if c.startswith("label_") or "post_open" in c}
    training = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "training")
    if os.path.isdir(training) and training not in sys.path:
        sys.path.insert(0, training)
    try:
        import frames  # pipelines/training/frames.py knows which contract columns are never model inputs
        banned |= set(frames.non_input_columns())
    except Exception:
        pass
    return banned


def read_feature_list(path):
    with open(path, encoding="utf-8") as fh:
        text = fh.read().strip()
    if text.startswith("["):
        return [str(c) for c in json.loads(text)]
    return [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]


def single_feature_check(df, exclude, S: Settings) -> pd.DataFrame:
    """How well each feature predicts an outcome on its own. A feature that is almost perfect alone usually carries
    information from after the PR opened."""
    d = df.sample(n=100_000, random_state=S.seed) if len(df) > 100_000 else df
    y = d["y"].to_numpy()
    recs = []
    for c in d.columns:
        if c in exclude or c.startswith(("_", "p_", "heuristic_")) or c == "y":
            continue
        s = d[c]
        if pd.api.types.is_bool_dtype(s):
            x = s.astype(float).to_numpy()
        elif pd.api.types.is_numeric_dtype(s):
            x = pd.to_numeric(s, errors="coerce").to_numpy(float)
        else:
            continue
        ok = np.isfinite(x)
        if ok.sum() < S.few_prs or np.unique(x[ok]).size < 2:
            continue
        best, cls = 0.5, ""
        for k in range(3):
            a = auroc_binary(y[ok] == k, x[ok])
            if np.isfinite(a) and max(a, 1 - a) > best:
                best, cls = max(a, 1 - a), CLASSES[k]
        recs.append(dict(feature=c, auroc_on_its_own=best, outcome=cls, filled_in=float(ok.mean())))
    if not recs:
        return pd.DataFrame(columns=["feature", "auroc_on_its_own", "outcome", "filled_in", "look_at_it"])
    t = pd.DataFrame(recs).sort_values("auroc_on_its_own", ascending=False).reset_index(drop=True)
    t["look_at_it"] = np.where(t["auroc_on_its_own"] > S.probe_auc, "YES", "")
    return t


# ===================================================================== checklist
def checklist(pred, m, groups_df, queue_checks, leak, S: Settings, has_tier) -> pd.DataFrame:
    rows = []

    def add(check, result, detail=""):
        rows.append(dict(check=check, result=result, detail=detail))

    c = pred["checks"]
    add("Prediction file is complete and valid", "PASS" if pred["file_ok"] else "FAIL",
        "" if pred["file_ok"] else
        f"{c['coverage']:.1%} of PRs have predictions (needs {S.min_coverage:.0%}); {c['duplicates']} duplicate rows; "
        f"{c['blank_rows']} blank rows; {c['outside_0_1']} outside 0-1; {c['not_adding_to_1']} not adding up to 1; "
        f"{c['reason_rows_not_adding_to_1']} reason rows not adding up to 1; "
        f"{c['answer_differs_between_seeds']} PRs whose answer differs between seeds")
    if leak is None:
        add("Model reads no banned columns", "not checked", "pass --features-used to check")
    else:
        add("Model reads no banned columns", "FAIL" if leak else "PASS",
            ("reads: " + ", ".join(leak)) if leak else "")
    add("Beats the base rate overall", "PASS" if m["skill_lo"] > 0 else "FAIL",
        f"skill {m['skill']:.4f}, 95% range down to {m['skill_lo']:.4f}")
    if groups_df.empty:
        add("Beats the base rate in every main group", "not checked", "no group columns in the CSV")
    else:
        t = groups_df[groups_df["grouping"].isin(MAIN_GROUPS) & ~groups_df["few_prs"]]
        neg = t[t["skill"] < 0]
        if t.empty:
            add("Beats the base rate in every main group", "not checked", "every group has too few PRs to judge")
        else:
            add("Beats the base rate in every main group", "PASS" if neg.empty else "FAIL",
                "worse in " + ", ".join(f"{a} = {b}" for a, b in zip(neg["grouping"], neg["group"])) if not neg.empty else "")
    slopes = [m[f"slope_{k}"] for k in SHORT]
    cal_ok = m["calibration_error"] <= S.ece_max and all(S.slope_lo <= v <= S.slope_hi for v in slopes if np.isfinite(v))
    add(f"Probabilities are honest (calibration error <= {S.ece_max}, slopes {S.slope_lo} to {S.slope_hi})",
        "PASS" if cal_ok else "FAIL",
        f"calibration error {m['calibration_error']:.4f}; slopes "
        + ", ".join(f"{v:.2f}" if np.isfinite(v) else "n/a" for v in slopes))
    tier_title = f"Honest within each repo tier (calibration error <= {S.ece_tier_max})"
    t = (groups_df[(groups_df["grouping"] == "tier") & ~groups_df["few_prs"]]
         if has_tier and not groups_df.empty else pd.DataFrame())
    if not has_tier:
        add(tier_title, "not checked", "no tier column")
    elif t.empty:
        add(tier_title, "not checked", "every tier has too few PRs to judge")
    else:
        bad = t[t["calibration_error"] > S.ece_tier_max]
        add(tier_title, "PASS" if bad.empty else "FAIL", ("off in " + ", ".join(bad["group"])) if not bad.empty else "")
    if not queue_checks:
        add("'Likely to go nowhere' list beats PR age alone", "not checked",
            "needs created_at, closed_at and moments with 10+ open PRs in a repo")
    for d, lo, hi, vs, metric in queue_checks:
        title = f"'Likely to go nowhere' list beats {SCORER_NAMES[vs]}"
        if not np.isfinite(lo):
            add(title, "not checked", "too few repos with busy queues to judge")
        else:
            add(title, "PASS" if lo > 0 else "FAIL", f"{QUEUE_NAMES[metric]}: {d:+.3f} (95% range {lo:+.3f} to {hi:+.3f})")
    lat = pred["latency"]
    if lat is None:
        add(f"Fast enough (95% of PRs scored within {S.latency_p95_ms:.0f} ms)", "not checked", "no latency_ms column")
    else:
        add(f"Fast enough (95% of PRs scored within {S.latency_p95_ms:.0f} ms)",
            "PASS" if lat["p95"] <= S.latency_p95_ms else "FAIL", f"95% within {lat['p95']:.0f} ms")
    ns = len(pred["seeds"]) if pred["seeds"] != [None] else 1
    add("Trained with 3 or more seeds", "PASS" if ns >= 3 else "WARN", f"{ns} seed(s)")
    return pd.DataFrame(rows)


# ===================================================================== run
def run(args, S: Settings) -> dict:
    notes = []
    loaded = load_csv(args, S, notes)
    df, raw, seeds = loaded["df"], loaded["raw"], loaded["seeds"]
    mix, train = outcome_mix(args, df, notes)
    overlap = overlap_check(df, train)
    y, codes = df["y"].to_numpy(), df["_code"].to_numpy()
    R = int(codes.max()) + 1
    repos = list(pd.Categorical(df["_repo"]).categories)
    pred = extract_predictions(raw, df, seeds, mix, S, loaded["label_conflicts"])
    effort = _effort(df)

    probs = {"model": pred["P"], "base_overall": np.tile(mix, (len(df), 1))}
    if np.isfinite(df["_created_s"]).any() and np.isfinite(df["_closed_s"]).any():
        probs["base_repo"] = repo_history_rate(df, mix, S.shrink_k)
    heuristic = None
    if all(c in raw.columns for c in HEURISTIC_COLS):
        hraw = raw[["_key", *HEURISTIC_COLS]].drop_duplicates("_key").rename(
            columns={h: c for h, c in zip(HEURISTIC_COLS, PROB_COLS)})
        h = extract_predictions(hraw, df, [None], mix, S)
        heuristic = probs["heuristic"] = h["P"]
        if not h["file_ok"]:
            notes.append(f"the heuristic_p_* columns have problems ({h['checks']['coverage']:.1%} of PRs filled in, "
                         f"{h['checks']['not_adding_to_1']} rows not adding up to 1); treat its numbers with care")
    elif any(c in raw.columns for c in HEURISTIC_COLS):
        notes.append("only some heuristic_p_* columns are present, so the simple heuristic is skipped")
    scored = {k: score_probs(P, y, codes, R, S) for k, P in probs.items()}
    ref = min((k for k in probs if k.startswith("base_")), key=lambda k: scored[k]["metrics"]["brier"])
    idx = boot_index(R, S.boot, S.seed)
    metrics = {}
    for k, sc in scored.items():
        mm = dict(sc["metrics"])
        mm["brier_lo"], mm["brier_hi"] = boot_mean_ci(sc["repo_brier"], idx)
        mm["skill"], mm["skill_lo"], mm["skill_hi"] = boot_skill(sc["repo_brier"], scored[ref]["repo_brier"], idx)
        mm["rps_skill"], mm["rps_skill_lo"], mm["rps_skill_hi"] = boot_skill(sc["repo_rps"], scored[ref]["repo_rps"], idx)
        metrics[k] = mm

    # ---- review queue simulation
    curves = None
    if train is not None and {"created_at", "closed_at"} <= set(train.columns):
        tr = train[train["outcome_3class"].isin(CLASSES)]
        curves = survival_curves(_utc_seconds(tr["created_at"]), _utc_seconds(tr["closed_at"]),
                                 tr["outcome_3class"].map({c: i for i, c in enumerate(CLASSES)}).to_numpy())
    elif np.isfinite(df["_closed_s"]).any():
        curves = survival_curves(df["_created_s"], df["_closed_s"], y)
    queues = simulate_queues(df, pred["P"], pred["P_seeds"], mix, S, curves, effort, heuristic)
    queue_avg, queue_repo, queue_checks = {}, {}, []
    if queues is not None:
        per = queues["moments"].groupby(["scorer", "repo"])[list(QUEUE_METRICS)].mean()
        for s in per.index.get_level_values(0).unique():
            t = per.loc[s].reindex(repos)
            queue_repo[s] = {mm: t[mm].to_numpy(float) for mm in QUEUE_METRICS}
            queue_avg[s] = {}
            for mm in QUEUE_METRICS:
                queue_avg[s][mm] = float(_nanmean(queue_repo[s][mm]))
                queue_avg[s][mm + "_lo"], queue_avg[s][mm + "_hi"] = boot_mean_ci(queue_repo[s][mm], idx)
        metric = "p_at_5_new" if np.isfinite(queue_repo["model"]["p_at_5_new"]).sum() >= S.few_repos else "p_at_5"
        rivals = (["pr_age_only"] if "pr_age_only" in queue_repo else ["oldest_first"]) + (
            ["heuristic"] if "heuristic" in queue_repo else [])
        for vs in rivals:
            queue_checks.append(boot_diff_ci(queue_repo["model"][metric], queue_repo[vs][metric], idx) + (vs, metric))
    else:
        notes.append("review-queue simulation skipped: it needs created_at, closed_at and moments with "
                     f"{S.min_open}+ open PRs in a repo")

    # ---- groups
    groups = build_groups(df)
    groups_df = (group_table(df, groups, pred["P"], scored["model"]["brier"], scored[ref]["brier"], queues, S)
                 if groups else pd.DataFrame())
    worst = []
    if not groups_df.empty:
        t = groups_df[~groups_df["few_prs"] & (groups_df["grouping"] != "month")]
        for gname, g in t.groupby("grouping", sort=False):
            worst.append(dict(grouping=gname, weakest_group=g.loc[g["skill"].idxmin(), "group"],
                              weakest_skill=g["skill"].min(), strongest_skill=g["skill"].max(),
                              gap=g["skill"].max() - g["skill"].min()))
    worst = pd.DataFrame(worst)

    # ---- seeds
    seed_df, seed_summary = pd.DataFrame(), None
    if len(pred["P_seeds"]) > 1:
        seed_df = pd.DataFrame([dict(seed=s, brier=float(_nanmean(group_mean(brier_rows(P, y), codes, R))))
                                for s, P in zip(pred["seeds"], pred["P_seeds"])])
        top = [P.argmax(axis=1) for P in pred["P_seeds"]]
        seed_summary = dict(seeds=len(top), brier_average=float(seed_df["brier"].mean()),
                            brier_spread=float(seed_df["brier"].std(ddof=1)),
                            top_guess_changes=float(np.mean([np.mean(a != b) for a, b in itertools.combinations(top, 2)])),
                            nowhere_list_overlap=queues["seed_overlap"] if queues else np.nan)

    # ---- why head, other labels, leaks
    wh = why_head(df, pred, _read(args.gold) if args.gold else None, S)
    other = score_other_labels(df, raw, pred, train, idx, S, notes, effort)
    banned = banned_columns(df.columns)
    leak = None
    if args.features_used:
        leak = sorted(set(read_feature_list(args.features_used)) & banned)
    feature_check = single_feature_check(df, banned | set(NON_FEATURES), S)
    checks = checklist(pred, metrics["model"], groups_df, queue_checks, leak, S, "tier" in groups)

    ctx = dict(name=args.model, df=df, notes=notes, overlap=overlap, ref=ref, metrics=metrics, scored=scored, pred=pred,
               queues=queues, queue_avg=queue_avg, queue_repo=queue_repo, queue_checks=queue_checks, groups_df=groups_df,
               worst=worst, seed_df=seed_df, seed_summary=seed_summary, why=wh, other=other, leak=leak,
               feature_check=feature_check, checks=checks, y=y, repos=repos, settings=S)
    report(ctx)
    write_outputs(args.out, ctx)
    return ctx


def _display(k, model_name):
    return model_name if k == "model" else BASE_NAMES.get(k, k)


def report(ctx):
    df, M, ref, S, name = ctx["df"], ctx["metrics"], ctx["ref"], ctx["settings"], ctx["name"]
    y = df["y"].to_numpy()
    mix = np.bincount(y, minlength=3) / len(y)
    _section(f"EVALUATION OF {name}")
    print(f"{_n(len(df), 'test PR')} in {_n(df['_repo'].nunique(), 'repo')}. What happened to them: "
          + ", ".join(f"{c} {v:.1%}" for c, v in zip(CLASSES, mix)))
    print(f"Ranges in [brackets] are 95% ranges from resampling the test repos {S.boot:,} times.")
    for n in ctx["notes"]:
        print("  note: " + n)
    if ctx["overlap"]:
        o = ctx["overlap"]
        print("  also in the training data: " + ", ".join(f"{k} {v:,}" for k, v in o.items())
              + ("   <-- the same PRs or commits are in both" if o.get("same PRs") or o.get("same head commits") else ""))

    _section("1. PREDICTION FILE")
    c = ctx["pred"]["checks"]
    print(_table(pd.DataFrame([dict(result="OK" if ctx["pred"]["file_ok"] else "PROBLEM",
                                    prs_with_predictions=f"{c['coverage']:.1%}", seeds=c["seeds"],
                                    duplicate_rows=c["duplicates"], blank_rows=c["blank_rows"],
                                    outside_0_1=c["outside_0_1"], not_adding_to_1=c["not_adding_to_1"],
                                    reason_columns="yes" if ctx["pred"]["R"] is not None else
                                    ("some missing" if c["partial_reason_columns"] else "no"))])))
    print("PRs with no prediction are given the usual outcome mix. Several seeds are averaged.")

    _section("2. OVERALL SCORE")
    order = ["model"] + sorted((k for k in M if k != "model"), key=lambda k: M[k]["brier"])
    print(_table(pd.DataFrame([dict(predictions=_display(k, name),
                                    brier=_ci(M[k]["brier"], M[k]["brier_lo"], M[k]["brier_hi"]),
                                    skill_vs_base_rate=_ci(M[k]["skill"], M[k]["skill_lo"], M[k]["skill_hi"]),
                                    ordered_score_rps=M[k]["rps"], log_loss=M[k]["log_loss"],
                                    top_guess_accuracy=M[k]["accuracy"]) for k in order])))
    print("brier: how far the probabilities are from what happened, averaged per repo (0 = perfect, 2 = worst).")
    print(f"skill: how much better than {_display(ref, name)} (the better base rate). Above 0 = better than guessing "
          "the usual mix.")
    if "heuristic" in M:
        print("simple heuristic: the heuristic_p_* columns of the CSV.")
    print("ordered score (RPS): like brier, but mixing up clean and rework costs less than clean and nowhere. "
          "log loss: punishes confident wrong answers hard. Lower is better for both.")

    _section("3. ARE THE PROBABILITIES HONEST?")
    m = M["model"]
    Pm = ctx["scored"]["model"]["P"]
    print(_table(pd.DataFrame([dict(outcome=CLASSES[k], calibration_error=m[f"calibration_error_{s}"],
                                    calibration_error_corrected=m[f"calibration_error_corrected_{s}"],
                                    slope=_slope_text(m[f"slope_{s}"], y == k, Pm[:, k]), offset=m[f"offset_{s}"])
                               for k, s in enumerate(SHORT)])))
    print(f"Average calibration error: {m['calibration_error']:.4f} (corrected: {m['calibration_error_corrected']:.4f})")
    print("calibration error: when the model says 30%, how far the real rate is from 30% (0 = perfect).")
    print("corrected: the same idea with the error that small groups of PRs add by chance taken out; trust it more "
          "when the test set is small.")
    print("slope: 1 is ideal; below 1 means the model is too confident, above 1 too timid. offset: 0 is ideal; below 0 "
          "means the probabilities run too high.")
    if not all(np.isfinite(m[f"slope_{s}"]) for s in SHORT):
        print("n/a (perfectly separated): the predictions split the PRs into yes and no without a single mistake, so "
              "no slope can be fitted. This happens on tiny test sets, not real ones.")

    _section("4. CAN IT TELL PRS APART?")
    print(_table(pd.DataFrame([dict(outcome=c_, share_of_prs=v, auroc=m[f"auroc_{s}"], auprc=m[f"auprc_{s}"])
                               for c_, s, v in zip(CLASSES, SHORT, mix)])))
    print(f"Top guess: accuracy {m['accuracy']:.4f}, balanced accuracy {m['balanced_accuracy']:.4f}, "
          f"F1 averaged over outcomes {m['macro_f1']:.4f}")
    cm = ctx["scored"]["model"]["confusion"]
    print("\nTop guess vs what happened (rows = what happened):")
    print(_table(pd.DataFrame(cm, columns=[f"guessed {s}" for s in SHORT]).assign(happened=list(SHORT))
                 [["happened"] + [f"guessed {s}" for s in SHORT]]))
    print("auroc: chance a PR with the outcome is ranked above one without it (0.5 = coin flip, 1 = perfect). "
          "auprc: how well it finds that outcome; random guessing gets the share of PRs.")

    _section("5. REVIEW QUEUE SIMULATION")
    qa = ctx["queue_avg"]
    if qa:
        mo = ctx["queues"]["moments"]
        print(f"{_n(mo[['repo', 't']].drop_duplicates().shape[0], 'moment')} replayed in {_n(mo['repo'].nunique(), 'repo')}, "
              f"each with {S.min_open}+ open PRs. {mo['open_prs_going_nowhere'].sum() / mo['open_prs'].sum():.1%} of the "
              "PRs waiting in these queues went nowhere.")
        print(_table(pd.DataFrame([dict(sorted_by=SCORER_NAMES[s], **{QUEUE_NAMES[mm]: _ci(qa[s][mm], qa[s][mm + "_lo"],
                                                                                          qa[s][mm + "_hi"], 3)
                                                                    for mm in QUEUE_METRICS})
                                   for s in SCORER_NAMES if s in qa])))
        print("nowhere list top-5 / top-10 hit rate: of the 5 (or 10) PRs flagged 'likely to go nowhere', the share "
              "that did.")
        print("dying PRs caught in top 10: share of the queue's goes-nowhere PRs that made the flagged top 10.")
        print("review-next top-5 / top-10 quality: how close the top of 'review next' is to the best order (1 = best).")
        print("effort-aware score: dying PRs found per line of code read, going down the list (1 = best).")
        print(f"new PRs only: the same, using only PRs opened in the last {S.new_days:g} days, because old open PRs are "
              "easy to spot as dying.")
        print("The model's and the heuristic's lists are updated for how long each PR has been open. 'PR age only' uses "
              "nothing but that; 'oldest first' and 'newest first' just sort by age; 'random order' shows what chance "
              "gets.")
    else:
        print(f"(skipped: needs created_at, closed_at and moments with {S.min_open}+ open PRs in a repo)")

    _section("6. WHY PRS GO NOWHERE")
    wh = ctx["why"]
    if wh is None:
        print("(skipped: no nowhere_reason column and no --gold file)")
    else:
        print("Checked against: " + wh["source"])
        if wh["summary"] is None:
            print("(the CSV has no p_reason_* columns)")
        else:
            s = wh["summary"]
            print(_table(pd.DataFrame([dict(prs=s["prs"], prs_with_a_known_reason=s["prs_with_a_known_reason"],
                                            f1_average=_ci(s["f1_average"], s["f1_average_lo"], s["f1_average_hi"]),
                                            f1_if_unknown_counts_as_wrong=s["f1_average_unknown_counts_as_wrong"],
                                            model_says_unknown=s["model_says_unknown"],
                                            really_unknown=s["really_unknown"],
                                            outcome_and_reason_brier=s["outcome_and_reason_brier"])])))
            print("\nPer reason:")
            print(_table(wh["per_reason"]))
            print("f1: balance of precision (when it says this reason, how often it is right) and recall (how many of "
                  "this reason it finds); 1 = perfect.")
        if wh["agreement"]:
            print("\nAgreement (kappa: 1 = full agreement, 0 = chance; 0.6 or more is good enough to trust the labels): "
                  + ", ".join(f"{k} {v:.3f}" for k, v in wh["agreement"].items()))

    _section("7. OTHER LABELS")
    ol = ctx["other"]
    t = ol["table"]
    if t.empty and ol["multiclass"].empty:
        print("(the CSV has no p_<label> columns besides the main and reason ones)")
    if not t.empty:
        print(_table(pd.DataFrame(dict(
            label=t["label"], prs=t["prs"].astype("Int64"), share_yes=t["share_yes"],
            brier=[_ci(*v) for v in zip(t["brier"], t["brier_lo"], t["brier_hi"])],
            skill_vs_base_rate=[_ci(*v) for v in zip(t["skill"], t["skill_lo"], t["skill_hi"])],
            auroc=t["auroc"], auprc=t["auprc"], calibration_error=t["calibration_error"],
            slope=[_slope_text(v, *ol["curves"][lab]) if lab in ol["curves"] else "n/a"
                   for v, lab in zip(t["slope"], t["label"])],
            caught_at_20pct_effort=t["caught_at_20pct_effort"], effort_score=t["effort_score"], note=t["note"]))))
        print("Only PRs where the label is known count. After-merge labels count only where they were observed, and a "
              "'no' only once its watch period ended (revert, hotfix, SZZ bug).")
        print("caught at 20% effort: share of bad PRs found by reviewing the riskiest PRs until 20% of all changed "
              "lines are read.")
    if not ol["multiclass"].empty:
        print("\nLabels with several answers:")
        print(_table(ol["multiclass"]))
    if not ol["consistency"].empty:
        print("\nPredictions that should agree with each other (CHECK when the average gap is above 0.05):")
        print(_table(ol["consistency"]))

    _section("8. RESULTS BY GROUP")
    gd = ctx["groups_df"]
    if gd is None or gd.empty:
        print("(no group columns in the CSV)")
    else:
        t = gd[gd["grouping"] != "month"].copy()
        t["grouping"] = t["grouping"].map(lambda g: GROUP_TITLES.get(g, g))
        print(_table(t[["grouping", "group", "prs", "repos", "few_prs", "skill", "calibration_error",
                        "false_flag_rate"]]))
        print(f"skill: against {_display(ref, name)} within the group. few_prs: under {S.few_prs} PRs or {S.few_repos} "
              "repos, shown but not judged. false_flag_rate: share of PRs that landed but were in a top-5 "
              "'likely to go nowhere' list.")
        if not ctx["worst"].empty:
            w = ctx["worst"].copy()
            w["grouping"] = w["grouping"].map(lambda g: GROUP_TITLES.get(g, g))
            print("\nWeakest group in each grouping:")
            print(_table(w))

    _section("9. RESULTS OVER TIME")
    if gd is not None and not gd.empty and "month" in set(gd["grouping"]):
        t = gd[(gd["grouping"] == "month") & (gd["group"] != "unknown")].sort_values("group")
        print(_table(t[["group", "prs", "few_prs", "skill", "calibration_error"]].rename(columns={"group": "month"})))
        print("A steady fall in skill over the months means the model is going stale.")
    else:
        print("(skipped: needs created_at)")

    _section("10. SEEDS")
    if ctx["seed_summary"] is None:
        print("(one seed: repeat each PR once per seed with a seed column to see how stable the model is)")
    else:
        print(_table(ctx["seed_df"]))
        print(_table(pd.DataFrame([ctx["seed_summary"]])))
        print("brier_spread: how much the score moves between seeds. top_guess_changes: share of PRs whose top guess "
              "changes between seeds. nowhere_list_overlap: how alike the flagged lists are (1 = identical).")

    _section("11. LEAK CHECKS")
    if ctx["leak"] is None:
        print("Banned columns: not checked (pass --features-used with the columns the model reads).")
    else:
        print("Banned columns the model reads: " + (", ".join(ctx["leak"]) if ctx["leak"] else "none"))
    fc = ctx["feature_check"]
    if fc is not None and not fc.empty:
        print(f"\nEach feature on its own (auroc above {S.probe_auc} is suspicious: it usually means the feature "
              "carries information from after the PR opened):")
        print(_table(fc.head(15)))
    else:
        print(f"\nEach feature on its own: none checked, because a feature needs a number in at least {S.few_prs} PRs"
              + (f" and this test set has only {len(df):,}." if len(df) < S.few_prs else "."))

    _section("12. CHECKLIST")
    for _, r in ctx["checks"].iterrows():
        print(f"  {r['result']:<12} {r['check']}" + (f"\n               {r['detail']}" if r["detail"] else ""))


def write_outputs(out, ctx):
    name, M, S, df = ctx["name"], ctx["metrics"], ctx["settings"], ctx["df"]
    pd.DataFrame([dict(predictions=_display(k, name), **M[k]) for k in M]).to_csv(
        os.path.join(out, "metrics.csv"), index=False)
    per = pd.DataFrame({"repo": ctx["repos"], "prs": np.bincount(df["_code"], minlength=len(ctx["repos"]))})
    per["brier"] = ctx["scored"]["model"]["repo_brier"]
    per["brier_base_rate"] = ctx["scored"][ctx["ref"]]["repo_brier"]
    with np.errstate(invalid="ignore", divide="ignore"):
        per["skill"] = 1 - per["brier"] / per["brier_base_rate"]
    for mm in QUEUE_METRICS:
        if "model" in ctx["queue_repo"]:
            per[mm] = ctx["queue_repo"]["model"][mm]
    per.to_csv(os.path.join(out, "per_repo.csv"), index=False)
    tables = (("groups.csv", ctx["groups_df"]), ("weakest_groups.csv", ctx["worst"]), ("seeds.csv", ctx["seed_df"]),
              ("feature_check.csv", ctx["feature_check"]), ("checklist.csv", ctx["checks"]),
              ("other_labels.csv", ctx["other"]["table"]), ("other_labels_multiclass.csv", ctx["other"]["multiclass"]),
              ("prediction_consistency.csv", ctx["other"]["consistency"]))
    for fname, t in tables:
        if t is not None and not t.empty:
            t.to_csv(os.path.join(out, fname), index=False)
    if ctx["queue_avg"]:
        pd.DataFrame([dict(sorted_by=SCORER_NAMES[s], **v) for s, v in ctx["queue_avg"].items()]).to_csv(
            os.path.join(out, "review_queue.csv"), index=False)
    wh = ctx["why"]
    if wh is not None and wh["summary"] is not None:
        pd.DataFrame([wh["summary"]]).to_csv(os.path.join(out, "why_nowhere.csv"), index=False)
        wh["per_reason"].to_csv(os.path.join(out, "why_nowhere_per_reason.csv"), index=False)
    _write_json(os.path.join(out, "summary.json"), dict(
        model=name, prs=len(df), repos=len(ctx["repos"]), base_rate_used=_display(ctx["ref"], name),
        notes=ctx["notes"], metrics=M["model"], base_rates={_display(k, name): M[k] for k in M if k != "model"},
        review_queue=ctx["queue_avg"], why_nowhere=(wh["summary"] if wh else None),
        agreement=(wh["agreement"] if wh else None), other_labels=ctx["other"]["table"].to_dict(orient="records"),
        checklist=ctx["checks"].to_dict(orient="records"), prediction_file=ctx["pred"]["checks"],
        latency_ms=ctx["pred"]["latency"], seeds=ctx["seed_summary"], settings=asdict(S)))
    made = make_charts(os.path.join(out, "charts"), ctx)
    _section("FILES")
    print(f"Everything is in {os.path.abspath(out)} (report.txt is this text)")
    for p in made:
        print("  " + p)


# ===================================================================== charts
PALETTE = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e"]
GREYS = {"base_overall": "#7f7f7f", "base_repo": "#b0b0b0", "heuristic": "#4d4d4d", "pr_age_only": "#9e9e9e",
         "oldest_first": "#8c8c8c", "newest_first": "#bdbdbd", "random_order": "#dcdcdc"}


def make_charts(out_dir, ctx) -> list:
    os.makedirs(out_dir, exist_ok=True)
    made = []
    name, M, y, ref, S = ctx["name"], ctx["metrics"], ctx["y"], ctx["ref"], ctx["settings"]
    P = ctx["scored"]["model"]["P"]

    def save(fig, fname):
        path = os.path.join(out_dir, fname)
        fig.tight_layout()
        fig.savefig(path, dpi=130)
        plt.close(fig)
        made.append(path)

    def overall():
        keys = sorted(M, key=lambda k: M[k]["brier"])[::-1]
        fig, (a1, a2) = plt.subplots(1, 2, figsize=(13, 1.4 + 0.6 * len(keys)))
        for row, k in enumerate(keys):
            v, lo, hi = M[k]["brier"], M[k]["brier_lo"], M[k]["brier_hi"]
            a1.errorbar(v, row, xerr=[[np.nan_to_num(v - lo)], [np.nan_to_num(hi - v)]], fmt="o", capsize=4, ms=7,
                        color=PALETTE[0] if k == "model" else GREYS[k])
        a1.set_yticks(range(len(keys)))
        a1.set_yticklabels([_display(k, name) for k in keys])
        a1.grid(axis="x", alpha=0.3)
        a1.set_xlabel("Brier score, averaged per repo (lower is better)")
        a1.set_title("Overall score with 95% range")
        others = [k for k in keys if k != ref]
        v = np.array([M[k]["skill"] for k in others])
        a2.barh([_display(k, name) for k in others], v, capsize=4,
                color=[PALETTE[0] if k == "model" else GREYS[k] for k in others],
                xerr=[np.nan_to_num(v - np.array([M[k]["skill_lo"] for k in others])),
                      np.nan_to_num(np.array([M[k]["skill_hi"] for k in others]) - v)])
        a2.axvline(0, color="k", lw=1)
        a2.set_xlabel(f"Skill vs {_display(ref, name)} (above 0 = better)")
        a2.set_title("How much better than guessing the usual mix")
        save(fig, "01_overall_score.png")

    def per_repo():
        with np.errstate(invalid="ignore", divide="ignore"):
            d = 1 - ctx["scored"]["model"]["repo_brier"] / ctx["scored"][ref]["repo_brier"]
        d = d[np.isfinite(d)]
        fig, ax = plt.subplots(figsize=(6.5, 4.8))
        ax.boxplot([d], showfliers=False)
        ax.scatter(1 + np.random.default_rng(0).uniform(-0.15, 0.15, len(d)), d, s=14, alpha=0.6, color=PALETTE[0])
        ax.set_xticks([1])
        ax.set_xticklabels([name])
        ax.axhline(0, color="k", lw=1)
        ax.set_ylabel(f"skill vs {_display(ref, name)}")
        ax.set_title(f"Skill in each test repo: better in {int((d > 0).sum())} of {len(d)}\n"
                     "(each dot is one repo; below 0 = worse than the base rate there)")
        save(fig, "02_skill_per_repo.png")

    def calibration():
        fig, axes = plt.subplots(2, 3, figsize=(15, 8.5), gridspec_kw={"height_ratios": [3, 1]})
        for k in range(3):
            ax, hx = axes[0, k], axes[1, k]
            ax.plot([0, 1], [0, 1], "k--", lw=1, label="perfectly honest")
            mp, fr = reliability_points((y == k).astype(float), P[:, k], S.ece_bins)
            ax.plot(mp, fr, "o-", ms=4, color=PALETTE[0],
                    label=f"{name} (calibration error {M['model'][f'calibration_error_{SHORT[k]}']:.3f})")
            hx.hist(P[:, k], bins=40, range=(0, 1), histtype="step", color=PALETTE[0])
            ax.set_xlim(0, 1)
            ax.set_ylim(0, 1)
            ax.set_title(f"{CLASSES[k]}: predicted vs actual")
            ax.set_xlabel("predicted probability (PRs in equal-size groups)")
            ax.set_ylabel("actual share")
            ax.legend(fontsize=8, loc="upper left")
            hx.set_yscale("log")
            hx.set_xlabel("predicted probability")
            hx.set_ylabel("PRs")
        save(fig, "03_calibration.png")

    def curves():
        fig, axes = plt.subplots(2, 3, figsize=(15, 9))
        for k in range(3):
            yb = y == k
            if not (0 < yb.sum() < len(yb)):
                continue
            fpr, tpr = roc_points(yb, P[:, k])
            axes[0, k].plot(fpr, tpr, color=PALETTE[0], label=f"auroc {M['model'][f'auroc_{SHORT[k]}']:.3f}")
            axes[0, k].plot([0, 1], [0, 1], "k--", lw=1, label="coin flip")
            axes[0, k].set_title(f"{CLASSES[k]}: finding it vs false alarms")
            axes[0, k].set_xlabel("share of other PRs wrongly flagged")
            axes[0, k].set_ylabel("share of these PRs found")
            rc, pr = pr_points(yb, P[:, k])
            axes[1, k].plot(rc, pr, color=PALETTE[0], label=f"auprc {M['model'][f'auprc_{SHORT[k]}']:.3f}")
            axes[1, k].axhline(yb.mean(), color="k", ls="--", lw=1, label=f"random guessing {yb.mean():.3f}")
            axes[1, k].set_title(f"{CLASSES[k]}: precision vs recall")
            axes[1, k].set_xlabel("recall (share found)")
            axes[1, k].set_ylabel("precision (share of flags that are right)")
            for a in (axes[0, k], axes[1, k]):
                a.legend(fontsize=8)
        save(fig, "04_roc_and_precision_recall.png")

    def confusion_chart():
        cm = ctx["scored"]["model"]["confusion"]
        norm = cm / np.maximum(cm.sum(axis=1, keepdims=True), 1)
        fig, ax = plt.subplots(figsize=(5.2, 4.6))
        ax.imshow(norm, cmap="Blues", vmin=0, vmax=1)
        for i in range(3):
            for j in range(3):
                ax.text(j, i, f"{norm[i, j]:.0%}\n({cm[i, j]:,})", ha="center", va="center", fontsize=8,
                        color="white" if norm[i, j] > 0.6 else "black")
        ax.set_xticks(range(3))
        ax.set_xticklabels(SHORT)
        ax.set_yticks(range(3))
        ax.set_yticklabels(SHORT)
        ax.set_xlabel("model's top guess")
        ax.set_ylabel("what happened")
        ax.set_title(f"Top guess vs what happened\naccuracy {M['model']['accuracy']:.3f}")
        save(fig, "05_top_guess_vs_outcome.png")

    def queue():
        qa = ctx["queue_avg"]
        if not qa:
            return
        scorers = [s for s in SCORER_NAMES if s in qa]
        mets = [mm for mm in QUEUE_METRICS if any(np.isfinite(qa[s][mm]) for s in scorers)]
        fig, ax = plt.subplots(figsize=(1.9 * len(mets) + 3, 5.4))
        w = 0.8 / len(scorers)
        for i, s in enumerate(scorers):
            v = np.array([qa[s][mm] for mm in mets])
            lo = np.array([qa[s][mm + "_lo"] for mm in mets])
            hi = np.array([qa[s][mm + "_hi"] for mm in mets])
            ax.bar(np.arange(len(mets)) + i * w - 0.4 + w / 2, v, w, label=SCORER_NAMES[s],
                   color=PALETTE[0] if s == "model" else GREYS[s],
                   yerr=[np.nan_to_num(v - lo), np.nan_to_num(hi - v)], capsize=2)
        ax.set_xticks(range(len(mets)))
        ax.set_xticklabels([QUEUE_NAMES[mm].replace(", ", ",\n") for mm in mets], fontsize=8)
        ax.set_ylim(0, 1)
        ax.set_ylabel("average over repos (95% range)")
        ax.set_title("Review-queue simulation (higher is better)")
        ax.legend(fontsize=8, ncol=len(scorers), loc="upper center", bbox_to_anchor=(0.5, -0.14))
        save(fig, "06_review_queue.png")

    def groups():
        gd = ctx["groups_df"]
        if gd is None or gd.empty:
            return
        for gname in [g for g in gd["grouping"].unique() if g != "month"]:
            t = gd[gd["grouping"] == gname]
            labels = [f"{g}\n({int(n):,} PRs){' *few' if few else ''}" for g, n, few in zip(t["group"], t["prs"], t["few_prs"])]
            colors = ["#cccccc" if few else PALETTE[0] for few in t["few_prs"]]
            fig, (a1, a2) = plt.subplots(1, 2, figsize=(max(12, 3.2 * len(t)), 4.8))
            a1.bar(range(len(t)), t["skill"], color=colors)
            a2.bar(range(len(t)), t["false_flag_rate"], color=colors)
            for a in (a1, a2):
                a.set_xticks(range(len(t)))
                a.set_xticklabels(labels, fontsize=8)
            a1.axhline(0, color="k", lw=1)
            a1.set_title(f"{GROUP_TITLES.get(gname, gname)}: skill vs base rate\n(below 0 = worse than guessing the usual mix)")
            a1.set_ylabel("skill")
            a2.set_title(f"{GROUP_TITLES.get(gname, gname)}: false flags\n(share of PRs that landed but were flagged as dying)")
            a2.set_ylabel("share of landed PRs")
            save(fig, f"07_group_{_safe_name(gname)}.png")

    def over_time():
        gd = ctx["groups_df"]
        if gd is None or gd.empty or "month" not in set(gd["grouping"]):
            return
        t = gd[(gd["grouping"] == "month") & (gd["group"] != "unknown")].sort_values("group")
        fig, ax = plt.subplots(figsize=(max(8, 0.45 * len(t) + 3), 4.5))
        ax.plot(t["group"], t["skill"], "-", color=PALETTE[0])
        few = t["few_prs"].astype(bool).to_numpy()
        ax.scatter(t["group"].to_numpy()[~few], t["skill"].to_numpy()[~few], color=PALETTE[0], s=18)
        ax.scatter(t["group"].to_numpy()[few], t["skill"].to_numpy()[few], facecolors="none", edgecolors=PALETTE[0], s=18)
        ax.axhline(0, color="k", lw=1)
        ax.set_ylabel("skill vs base rate")
        ax.set_title("Skill by month the PR was opened (hollow = few PRs)")
        ax.tick_params(axis="x", rotation=60)
        save(fig, "08_skill_over_time.png")

    def why():
        wh = ctx["why"]
        if not wh or wh["confusion"] is None:
            return
        cm = wh["confusion"]
        norm = cm / np.maximum(cm.sum(axis=1, keepdims=True), 1)
        fig, ax = plt.subplots(figsize=(5.8, 5.2))
        ax.imshow(norm, cmap="Purples", vmin=0, vmax=1)
        for i in range(5):
            for j in range(5):
                ax.text(j, i, f"{norm[i, j]:.0%}", ha="center", va="center", fontsize=7,
                        color="white" if norm[i, j] > 0.6 else "black")
        ax.set_xticks(range(5))
        ax.set_xticklabels(REASONS, rotation=40, ha="right", fontsize=8)
        ax.set_yticks(range(5))
        ax.set_yticklabels(REASONS, fontsize=8)
        ax.set_xlabel("model's top reason")
        ax.set_ylabel("real reason")
        ax.set_title("Why PRs go nowhere: model vs real reason")
        save(fig, "09_why_nowhere.png")

    def other_labels():
        t = ctx["other"]["table"]
        if t.empty:
            return
        t = t[t["skill"].notna()].reset_index(drop=True)
        if t.empty:
            return
        yy = np.arange(len(t))
        fig, (a1, a2) = plt.subplots(1, 2, figsize=(14, 1.4 + 0.45 * len(t)))
        a1.barh(yy, t["skill"], color=PALETTE[0], capsize=3,
                xerr=[np.nan_to_num(t["skill"] - t["skill_lo"]), np.nan_to_num(t["skill_hi"] - t["skill"])])
        a1.axvline(0, color="k", lw=1)
        a1.set_yticks(yy)
        a1.set_yticklabels([f"{lab} ({int(n):,} PRs, {r:.1%} yes)"
                            for lab, n, r in zip(t["label"], t["prs"], t["share_yes"])], fontsize=8)
        a1.invert_yaxis()
        a1.set_xlabel("skill vs the label's base rate (above 0 = better)")
        a1.set_title("Other labels: skill (95% range)")
        a2.barh(yy, t["auroc"], color=PALETTE[1])
        a2.axvline(0.5, color="k", lw=1, ls="--")
        a2.set_xlim(0.4, 1.0)
        a2.set_yticks(yy)
        a2.set_yticklabels(t["label"], fontsize=8)
        a2.invert_yaxis()
        a2.set_xlabel("auroc (0.5 = coin flip, 1 = perfect)")
        a2.set_title("Other labels: telling yes from no")
        save(fig, "10_other_labels.png")

    def other_calibration():
        cur = ctx["other"]["curves"]
        if not cur:
            return
        ncol = min(4, len(cur))
        nrow = math.ceil(len(cur) / ncol)
        fig, axes = plt.subplots(nrow, ncol, figsize=(3.8 * ncol, 3.5 * nrow), squeeze=False)
        for ax, (label, (yb, pp)) in zip(axes.flat, cur.items()):
            mp, fr = reliability_points(yb, pp, S.ece_bins)
            top = min(1.0, max(0.05, float(np.nanmax(np.r_[mp, fr])) * 1.1))
            ax.plot([0, top], [0, top], "k--", lw=1)
            ax.plot(mp, fr, "o-", ms=3, color=PALETTE[0])
            ax.set_xlim(0, top)
            ax.set_ylim(0, top)
            ax.set_title(label, fontsize=9)
            ax.set_xlabel("predicted", fontsize=8)
            ax.set_ylabel("actual", fontsize=8)
        for ax in list(axes.flat)[len(cur):]:
            ax.axis("off")
        fig.suptitle("Other labels: predicted vs actual (dashed line = perfectly honest; axes zoomed in)")
        save(fig, "11_other_labels_calibration.png")

    def feature_check():
        fc = ctx["feature_check"]
        if fc is None or fc.empty:
            return
        t = fc.head(20)[::-1]
        fig, ax = plt.subplots(figsize=(9, 0.32 * len(t) + 1.6))
        ax.barh(t["feature"], t["auroc_on_its_own"], color=np.where(t["look_at_it"] == "YES", "#d62728", "#7f7f7f"))
        ax.axvline(S.probe_auc, color="k", ls="--", lw=1, label=f"suspicious above {S.probe_auc}")
        ax.set_xlim(0.5, 1.0)
        ax.set_xlabel("auroc of the feature on its own")
        ax.set_title("Leak check: features that predict the outcome too well on their own")
        ax.legend(fontsize=8)
        save(fig, "12_leak_check.png")

    def seeds():
        sd = ctx["seed_df"]
        if sd is None or sd.empty:
            return
        v = sd["brier"].to_numpy()
        fig, ax = plt.subplots(figsize=(5, 4.2))
        ax.scatter(np.zeros(len(v)), v, color=PALETTE[0], s=30)
        ax.errorbar(0.15, v.mean(), yerr=v.std(ddof=1), fmt="s", color="k", capsize=5)
        ax.set_xticks([0])
        ax.set_xticklabels([name])
        ax.set_xlim(-0.5, 0.6)
        ax.set_ylabel("Brier score")
        ax.set_title(f"Score for each training seed\n(dots = seeds; square = average, spread {v.std(ddof=1):.4f})")
        save(fig, "13_seeds.png")

    for fn in (overall, per_repo, calibration, curves, confusion_chart, queue, groups, over_time, why, other_labels,
               other_calibration, feature_check, seeds):
        try:
            fn()
        except Exception as e:      # one broken chart must not stop the rest
            print(f"  chart skipped ({fn.__name__}): {type(e).__name__}: {e}")
    return made


# ===================================================================== command line
def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], epilog=__doc__.split("\n\n", 1)[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", required=True, help="test PRs with their answers and the model's predictions")
    ap.add_argument("--out", required=True, help="folder for the report, tables and charts")
    ap.add_argument("--model", default="model", help="the model's name, used in the report and charts")
    ap.add_argument("--train", help="training data: the usual outcome mix and how long PRs stay open")
    ap.add_argument("--gold", help="human-checked reasons why PRs went nowhere")
    ap.add_argument("--features-used", help="the columns the model reads, one per line, to check for banned ones")
    ap.add_argument("--all-authors", action="store_true",
                    help="keep PRs by maintainers and bots (by default only outside contributors are evaluated)")
    ap.add_argument("--boot", type=int, default=2000, help="resamples of the test repos for the 95%% ranges")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    args.out = os.path.normpath(args.out)      # one kind of slash in every printed path
    os.makedirs(args.out, exist_ok=True)
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    tee = _Tee(os.path.join(args.out, "report.txt"), sys.stdout)
    sys.stdout = tee
    try:
        run(args, Settings(boot=args.boot))
    finally:
        sys.stdout = tee.stream
        tee.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
