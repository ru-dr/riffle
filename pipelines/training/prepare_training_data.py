#!/usr/bin/env python
"""
prepare_training_data.py - build the clean at-open training table from the mined data, as one CSV.

    python pipelines/training/prepare_training_data.py
    python pipelines/training/prepare_training_data.py --since 2023-07-01 --min-age-days 180 --out data/training

Rows      every author's PRs except bots and authors who had already merged or closed others' PRs (point in time),
          opened on or after --since, opened at least --min-age-days before the data was collected (so slow PRs
          have had time to land), and only PRs whose at-open diff could be recovered. author_association is not used
          to choose rows: it is the author's status today, not at open (--outside-only brings that old filter back).
Columns   drops the columns that give the outcome away (author_association, the semgrep columns), the features read
          from the PR body as it is today rather than at open (BODY; --keep-body keeps them) and the repo signals the
          miner builds from today's author_association (OUTSIDE_SIGNALS; kept with --outside-only), the agent flags
          partly read from the body (AGENT_FLAGS) and author_prs_merged_repo, whose blank says "merged" (both found by
          Ananth; --keep-agent-flags keeps them), fills the blank
          fork flag (a blank means the fork was deleted later), keeps the LLM flags with their blanks (--no-llm
          leaves them out), drops empty and constant columns, turns short text columns into 0/1 columns and adds
          the engineered features from explore_features.engineer(), so training and scoring build them the same way.
          Near-copies (features whose values move together almost perfectly) keep only the one that predicts the
          outcome best; a set of near-copies that all predict nothing is dropped whole (--keep-duplicates keeps them).
          llm_flags_status (not a feature) says why a PR has no flags: skipped_trivial_diff (docs or tests only;
          the scorer skips those too), llm_off_for_run (mined without --llm), or skipped_other.
          filled_values (not a feature) lists, per PR, the features whose blank was filled (the fork flag and the
          random fills of mining-artifact blanks), so explanations never quote a filled value as a fact.
History   ah_* (the author's earlier PRs in this repo) and rh* (the repo's recent behaviour, and how it treated outside
          authors and outside first-timers), ported from Ananth's pipeline (ananth_pipeline/pipeline.ipynb, section 3):
          computed over every PR in the mined table (bots, maintainers and 'final' rows included: their outcomes are
          real), counting a PR only if it closed strictly before this PR opened; a PR with no closed_at never lends
          its outcome. --no-history-v2 leaves them out. The training scripts still add their own hist_* and rhist_*.
Weights   sample_weight caps each repo's total weight at --repo-cap PRs, so a few big repos do not dominate.
          The outcome classes are left as they are: equalising them would make the probabilities wrong.
          Only PRs whose at-open diff is exact (open_snapshot 'exact') carry weight; the others get 0 but stay in the
          table, so they are still evaluated and still count in history. Recovered diffs mark rework PRs (Ananth: a
          model tells them apart at AUROC 0.64 from blanks alone) and never happen when a new PR is scored. Exact PRs
          are reweighted so each repo keeps its outcome mix (--train-recovered gives every row weight again).
Splits    fold (0 to --folds - 1): every repo in exactly one fold, for cross-validation across repos;
          time_split: train before --time-cut, test from it; frames_split: frames.py's repo split.
Labels    outcome_3class and y (0 = lands_clean, 1 = lands_after_rework, 2 = goes_nowhere); nowhere_reason and
          reason_clean (an "abandoned" PR closed within an hour becomes withdrawn_or_rejected); every label_*
          column with its observation column, for the other heads.

Writes to --out (default data/training at the repo root, which is gitignored):
  train_at_open.csv   one row per PR: ids and dates, labels, weights and splits, then the features
  features.txt        the model inputs, one per line (also works as the evaluation script's --features-used)
  manifest.json       every rule with its row count, every dropped column with the reason, and the leak check

A last leak check flags any feature that predicts the outcome too well on its own, or whose blanks do;
--drop-flagged drops those too. Needs numpy, pandas and pyarrow, plus what explore_features.py imports.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import frames  # noqa: E402  the team's loader: which columns are never inputs, and the repo split
from explore_features import CLASSES, OUTSIDE, _num, _times, auroc, engineer, feature_groups  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
LEAKY = {
    "author_association": "the author's status today, not at open: a first-timer who got merged now shows as "
                          "CONTRIBUTOR, so it gives the outcome away",
    "semgrep_findings_new": "filled mostly for merged PRs, so being filled gives the outcome away",
    "semgrep_high_conf_security_new": "filled mostly for merged PRs, so being filled gives the outcome away",
    "semgrep_cwe_top25_new": "filled mostly for merged PRs, so being filled gives the outcome away",
    "first_time_author": "built from author_association",
    "agent_and_first_time": "built from author_association",
}
# The miner reads the PR body as it is at mining time, not as it was at open (mine_repo.py: body_features(pr["body"]),
# and the title-plus-body tokens behind the similarity features), so edits made during review or after close reach
# these features. Dropped unless --keep-body.
BODY = {
    "body_len": "read from the PR body as it is today, not at open",
    "has_description": "built from body_len, which is read from the PR body as it is today, not at open",
    "has_linked_issue": "read from the PR body as it is today, not at open",
    "body_headings": "read from the PR body as it is today, not at open",
    "body_checklist_items": "read from the PR body as it is today, not at open",
    "body_has_test_plan": "read from the PR body as it is today, not at open",
    "agent_boilerplate": "read from the PR body as it is today, not at open",
    "max_sim_open_prs": "similarity of title and body tokens; the body is read as it is today, not at open",
    "max_sim_recent_merged": "similarity of title and body tokens; the body is read as it is today, not at open",
    "similar_open_minus_merged": "built from the similarity features, whose body is read as it is today, not at open",
}
# Repo signals the miner computes over "outside" PRs, decided by author_association, which is the author's status
# today (merge_signals.sidecar). Dropped unless --outside-only.
OUTSIDE_SIGNALS = {
    "repo_outside_merge_rate": "built over PRs chosen by author_association, the author's status today",
    "repo_outside_closed_count": "built over PRs chosen by author_association, the author's status today",
}
# Copied to the table as evaluation columns (never model inputs) when they are not features: eval_script.py groups by
# them.
# Found by Ananth (ananth_pipeline/WRITEUP.md). Dropped unless --keep-agent-flags.
AGENT_FLAGS = {
    "ai_agent_authored": "partly detected from watermarks in the PR body as it is today, not at open",
    "agent_name": "partly detected from watermarks in the PR body as it is today, not at open",
    "agent_and_new_author": "built from ai_agent_authored, which is partly read from the PR body as it is today",
    "author_prs_merged_repo": "blank only on merged PRs, so a blank says merged; ah_* author history replaces it",
}
EVAL_ONLY = ("repo_outside_closed_count", "ai_agent_authored")
FILL = {"head_from_fork": (1.0, "blank means the head repo was deleted later, which only happens to forks")}
# Blanks that come from how the data was mined, not from the PR: they never happen when a new PR is scored, but in the
# mined data they line up with the outcome. random_fill() replaces them with values drawn from the same repo's real
# values, so a blank tells the model nothing (no labels are used).
RANDOM_FILL = {
    "author_prs_merged_repo": "blank when the PR's head commit was not in the clone, which happened mostly for merged PRs",
    "ccn_delta": "blank when the code could not be analysed, which happened mostly for PRs that went nowhere",
    "n_funcs_modified": "blank when the code could not be analysed, which happened mostly for PRs that went nowhere",
    "AGE": "blank when the files' history could not be read, which happened mostly for PRs that went nowhere",
    "max_file_post_merge_ci_fail_rate": "blank when no CI history was kept (mostly older PRs), so it would tell the model the date",
}
LEGIT_BLANKS = {"author_days_since_last_change": "blank = the author's first change in the repo, known at open"}
PROTECTED = {"ai_agent_authored"}   # never pruned: rare overall, but central to the agent-PR slice
META = ("repo", "pr_number", "created_at", "closed_at", "observed_through", "domain", "miner_version",
        "llm_prompt_version")
LABELS = ("outcome_3class", "nowhere_reason", "close_reason", "close_reason_source")
OBS_COLUMNS = ("label_bad_observed", "ci_observed", "revert_window_closed", "hotfix_window_closed",
               "szz_window_closed", "ci_fail_flaky_only")
MAX_TEXT_VALUES = 15     # text columns with at most this many values become 0/1 columns
SUSPICIOUS_AUROC = 0.85
BLANK_GAP = 0.15         # blanks whose goes-nowhere rate differs this much from filled rows are flagged
MIN_ROWS = 200


def _flag(df, col):
    if col not in df.columns:
        return np.zeros(len(df), bool)
    return np.array(df[col].eq(True).fillna(False), dtype=bool)


def _sha256(path, block=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(block), b""):
            h.update(chunk)
    return h.hexdigest()


def _clean_name(v):
    return re.sub(r"[^A-Za-z0-9_]+", "_", str(v)).strip("_") or "blank"


def random_fill(p, cols, seed=0):
    """Fills the blanks listed in RANDOM_FILL with values drawn at random (seeded) from the same repo's real values for
    that column, or from all repos when a repo has fewer than 20. Uses no labels, so it cannot leak the outcome."""
    rng = np.random.default_rng(seed)
    p = p.copy()
    done, masks = {}, {}
    for c in cols:
        if c not in p.columns:
            continue
        x = _num(p[c])
        if x is None:
            continue
        x = np.array(x, dtype=float)
        miss = np.isnan(x)
        if not miss.any() or miss.all():
            continue
        everyone = x[~miss]
        for _, idx in p.groupby("repo", sort=True).indices.items():
            holes = idx[miss[idx]]
            if len(holes):
                pool = x[idx][~miss[idx]]
                x[holes] = rng.choice(pool if pool.size >= 20 else everyone, size=len(holes), replace=True)
        p[c] = x
        done[c] = dict(rows=int(miss.sum()), why=cols[c])
        masks[c] = miss
    return p, done, masks


def author_keys(p):
    """A short anonymous key per PR author (hash of the git email), for features about the author's earlier PRs."""
    if "author_email" not in p.columns:
        return np.full(len(p), None, dtype=object)
    return p["author_email"].map(lambda e: hashlib.sha1(e.strip().lower().encode("utf-8")).hexdigest()[:12]
                                 if isinstance(e, str) and e.strip() else None).to_numpy()


def collected_at(d):
    """When each PR's data was collected. mined_at is set on every row; observed_through is blank for every PR that
    never merged (mine_repo.py), so it only fills gaps. Rows still blank get the latest collection date."""
    t = None
    for col in ("mined_at", "observed_through"):
        if col in d.columns:
            v = _times(d[col])
            t = v if t is None else t.fillna(v)
    if t is None or t.isna().all():
        return None
    return t.fillna(t.max())


# ===================================================================== history (ported from Ananth's pipeline)
DAY_S = 86400
OFF = np.int64(1) << 36           # key * OFF + seconds: one sortable number per (key, time)
NOT_YET = np.int64(1) << 34       # "closes" far in the future: still open, or close time unknown
HISTORY_V2 = ("ah_n", "ah_merged", "ah_nowhere", "ah_clean_rate", "ah_rework_rate", "ah_nowhere_rate", "ah_open_now",
              "ah_first_pr", "ah_days_since_prev_pr", "ah_tenure_days", "rh90_n", "rh90_clean_rate",
              "rh90_rework_rate", "rh90_nowhere_rate", "rh_open_now", "rh30_opened", "rh90_mean_log_hours_to_close",
              "rh180_ft_n", "rh180_ft_clean_rate", "rh180_ft_nowhere_rate", "rh180_out_n", "rh180_out_clean_rate",
              "rh180_out_nowhere_rate")


def secs(s):
    """Seconds since 1970, whatever unit the timestamps are stored in (parquet often gives microseconds); a missing
    time comes back as NaN."""
    t = pd.to_datetime(s, utc=True, errors="coerce")
    return ((t - pd.Timestamp(0, tz="UTC")) / pd.Timedelta(seconds=1)).to_numpy(float)


def _own_keys(key):
    """Unknown keys (< 0) each get a key of their own, so their events never count for anyone else."""
    key = np.asarray(key, np.int64)
    k = key.copy()
    bad = key < 0
    k[bad] = key.max(initial=0) + 1 + np.arange(int(bad.sum()))
    return key, k


def prior_sum(key, t_query, t_event, W, window=None):
    """For each row i: the sum of W[j] over rows j with key[j] == key[i] and t_event[j] < t_query[i] (and
    t_event[j] >= t_query[i] - window when given). Unknown key: NaN. Times are whole seconds (int64)."""
    key, k = _own_keys(key)
    W = np.asarray(W, float).reshape(len(key), -1)
    ev = k * OFF + t_event
    order = np.argsort(ev, kind="stable")
    evs = ev[order]
    cum = np.vstack([np.zeros((1, W.shape[1])), np.cumsum(W[order], axis=0)])
    q = k * OFF + t_query
    lo = k * OFF if window is None else np.maximum(q - window, k * OFF)
    out = cum[np.searchsorted(evs, q, side="left")] - cum[np.searchsorted(evs, lo, side="left")]
    out[key < 0] = np.nan
    return out


def prev_event_gap(key, t_query, t_event):
    """Seconds since the latest event of the same key strictly before t_query (NaN when none)."""
    key, k = _own_keys(key)
    evs = np.sort(k * OFF + t_event)
    pos = np.searchsorted(evs, k * OFF + t_query, side="left") - 1
    prev = evs[np.clip(pos, 0, None)]
    ok = (pos >= 0) & (prev // OFF == k) & (key >= 0)
    return np.where(ok, t_query - (prev - k * OFF), np.nan).astype(float)


def first_event_gap(key, t_query, t_event):
    """Seconds since the key's first event, if that event is strictly before t_query (NaN otherwise)."""
    key, k = _own_keys(key)
    first = pd.Series(t_event).groupby(k).transform("min").to_numpy()
    return np.where((first < t_query) & (key >= 0), t_query - first, np.nan).astype(float)


def _rates(counts, n, prior, strength):
    """Class rates shrunk towards `prior`: (count + strength * prior) / (n + strength)."""
    return (counts + strength * prior) / (n[:, None] + strength)


def history_v2(d):
    """Ananth's ah_* and rh* features (pipeline.ipynb, prepare()) for every row of the mined table, point in time:
    a past PR counts only if it closed strictly before this PR opened; a PR with no closed_at never lends an outcome
    (when its outcome became known is unknown). One change from Ananth's code: for the open-now counts, a PR with
    no closed_at counts as open from the day it opened, as it was (still open when mined). His version left such
    PRs out of the open counts, which would leave ah_open_now and rh_open_now near 0 in production, where every
    open PR has no closed_at.
    'Outside' = not a bot and not a confirmed maintainer before this PR opened (both point in time)."""
    n = len(d)
    out = pd.DataFrame(index=d.index, columns=list(HISTORY_V2), dtype=float)
    if "repo" not in d.columns or "created_at" not in d.columns:
        return out
    y = d["outcome_3class"].map({c: i for i, c in enumerate(CLASSES)}).fillna(-1).astype(int).to_numpy() \
        if "outcome_3class" in d.columns else np.full(n, -1)
    known = y >= 0
    t_open_f = secs(d["created_at"])
    has_open = np.isfinite(t_open_f)
    t = np.where(has_open, t_open_f, 0).astype(np.int64)
    tc_f = secs(d["closed_at"]) if "closed_at" in d.columns else np.full(n, np.nan)
    tc = np.where(np.isfinite(tc_f), tc_f, NOT_YET).astype(np.int64)
    tc = np.maximum(tc, t)                                      # a close stamped before the open is not earlier
    hist_ok = known & np.isfinite(tc_f) & has_open
    Y = np.eye(3)[np.clip(y, 0, 2)] * hist_ok[:, None]
    ones = has_open.astype(float)
    email = d["author_email"] if "author_email" in d.columns else pd.Series([None] * n, index=d.index)
    akey = email.map(lambda e: e.strip().lower() if isinstance(e, str) and e.strip() else None)
    repo = pd.factorize(d["repo"].astype(str))[0]
    ar = pd.factorize(d["repo"].astype(str) + "\x00" + akey.fillna("\x01"))[0]
    ar[akey.isna().to_numpy()] = -1
    r365 = prior_sum(repo, t, tc, Y, 365 * DAY_S)
    prior = (r365 + 1.0) / (r365.sum(1, keepdims=True) + 3.0)
    a = prior_sum(ar, t, tc, Y)
    an = a.sum(1)
    opened = prior_sum(ar, t, t, ones)[:, 0]
    out["ah_n"], out["ah_merged"], out["ah_nowhere"] = an, a[:, 0] + a[:, 1], a[:, 2]
    rate = _rates(a, an, prior, 3.0)
    out["ah_clean_rate"], out["ah_rework_rate"], out["ah_nowhere_rate"] = rate.T
    out["ah_open_now"] = prior_sum(ar, t, t, ones)[:, 0] - prior_sum(ar, t, tc, ones)[:, 0]
    out["ah_first_pr"] = ((an == 0) & (opened == 0)).astype(float)
    out["ah_days_since_prev_pr"] = prev_event_gap(ar, t, t) / DAY_S
    out["ah_tenure_days"] = first_event_gap(ar, t, t) / DAY_S
    out.loc[akey.isna().to_numpy(), [c for c in HISTORY_V2 if c.startswith("ah_")]] = np.nan
    r90 = prior_sum(repo, t, tc, Y, 90 * DAY_S)
    rn = r90.sum(1)
    out["rh90_n"] = rn
    rr = np.where(rn[:, None] >= 5, r90 / np.maximum(rn, 1)[:, None], np.nan)
    out["rh90_clean_rate"], out["rh90_rework_rate"], out["rh90_nowhere_rate"] = rr.T
    out["rh_open_now"] = prior_sum(repo, t, t, ones)[:, 0] - prior_sum(repo, t, tc, ones)[:, 0]
    out["rh30_opened"] = prior_sum(repo, t, t, ones, 30 * DAY_S)[:, 0]
    s = prior_sum(repo, t, tc, np.c_[np.log1p((tc - t) / 3600.0) * hist_ok, hist_ok], 90 * DAY_S)
    out["rh90_mean_log_hours_to_close"] = np.where(s[:, 1] >= 5, s[:, 0] / np.maximum(s[:, 1], 1), np.nan)
    base = np.where(np.isnan(rr), prior, rr)
    outside = ~_flag(d, "author_is_bot") & ~_flag(d, "author_confirmed_maintainer")
    for name, mask in (("ft", outside & (np.nan_to_num(an) == 0)), ("out", outside)):
        c = prior_sum(repo, t, tc, Y * mask[:, None], 180 * DAY_S)
        nn = c.sum(1)
        r = _rates(c, nn, base, 5.0)
        out[f"rh180_{name}_n"] = nn
        out[f"rh180_{name}_clean_rate"], out[f"rh180_{name}_nowhere_rate"] = r[:, 0], r[:, 2]
    out.loc[~has_open] = np.nan
    return out


# ===================================================================== rows
def select_rows(d, args, llm_cols):
    """The training rows, with the count left after each rule."""
    steps = [("all PRs", len(d))]

    def step(name, mask):
        nonlocal keep
        keep &= mask
        steps.append((name, int(keep.sum())))

    keep = np.ones(len(d), bool)
    step("with an outcome", np.array(d["outcome_3class"].isin(CLASSES), dtype=bool))
    if getattr(args, "outside_only", False) and "author_association" in d.columns:
        step("by outside contributors (author_association, today's status: --outside-only)",
             np.array(d["author_association"].isin(OUTSIDE), dtype=bool))
    step("not bots", ~_flag(d, "author_is_bot"))
    step("author had not already merged or closed others' PRs", ~_flag(d, "author_confirmed_maintainer"))
    created = _times(d["created_at"])
    if args.since:
        step(f"opened on or after {args.since}",
             np.array((created >= pd.Timestamp(args.since, tz="UTC")).fillna(False), dtype=bool))
    collected = collected_at(d) if args.min_age_days > 0 else None
    if collected is not None:
        age = (collected - created).dt.days
        step(f"opened {args.min_age_days}+ days before the data was collected (mined_at)",
             np.array((age >= args.min_age_days).fillna(False), dtype=bool))
    if "open_snapshot" in d.columns:
        step("at-open diff recovered (open_snapshot is not 'final')",
             ~np.array(d["open_snapshot"].eq("final").fillna(False), dtype=bool))
    p = d[keep].reset_index(drop=True).copy()
    p["y"] = p["outcome_3class"].map({c: i for i, c in enumerate(CLASSES)}).astype(int)
    return p, steps


# ===================================================================== columns
def dropped_by_rule(args):
    """The columns dropped by name, with the reason: LEAKY always, BODY unless --keep-body, OUTSIDE_SIGNALS unless
    --outside-only, AGENT_FLAGS unless --keep-agent-flags."""
    drop = dict(LEAKY)
    if not getattr(args, "keep_body", False):
        drop.update(BODY)
    if not getattr(args, "outside_only", False):
        drop.update(OUTSIDE_SIGNALS)
    if not getattr(args, "keep_agent_flags", False):
        drop.update(AGENT_FLAGS)
    return drop


def build_features(p, args, cands, groups):
    """The feature table, and what happened to every candidate column."""
    dropped, filled, expanded = {}, {}, {}
    drop = dropped_by_rule(args)
    X = {}
    for c in cands:
        if c in drop:
            dropped[c] = drop[c]
            continue
        if groups.get(c) == "llm_flags" and args.no_llm:
            dropped[c] = "LLM flag, left out with --no-llm"
            continue
        s = p[c]
        x = _num(s)
        if c in FILL:
            value, why = FILL[c]
            if x is not None:
                filled[c] = dict(value=value, rows=int(np.isnan(x).sum()), why=why)
                x = np.where(np.isnan(x), value, x)
        if x is None:
            vals = s.dropna().astype(str)
            uniq = sorted(vals.unique())
            if len(uniq) > MAX_TEXT_VALUES:
                dropped[c] = f"text with {len(uniq)} different values"
                continue
            txt = s.astype(str)
            for u in uniq:
                X[f"{c}__{_clean_name(u)}"] = np.where(s.isna().to_numpy(), np.nan, (txt == u).astype(float).to_numpy())
            expanded[c] = uniq
            continue
        X[c] = x
    eng, spec = engineer(p)
    engineered = []
    for sp in spec:
        name = sp["feature"]
        if name in drop:
            if name not in LEAKY:
                dropped[name] = drop[name]
            continue
        X[name] = _num(eng[name])
        engineered.append(dict(feature=name, meaning=sp["meaning"], built_from=sp["built_from"]))
    if "author_days_since_last_change" in p.columns:
        X["author_first_change_in_repo"] = p["author_days_since_last_change"].isna().astype(float).to_numpy()
        engineered.append(dict(feature="author_first_change_in_repo", meaning="the author has never changed the repo "
                               "before (author_days_since_last_change is blank)", built_from="author_days_since_last_change"))
    for c in list(X):
        v = X[c]
        ok = np.isfinite(v)
        if ok.mean() < 0.05:
            dropped[c] = f"empty (filled for {ok.mean():.1%} of the rows)"
            del X[c]
        elif np.unique(v[ok]).size <= 1:
            dropped[c] = "constant on these rows"
            del X[c]
    return X, dropped, filled, expanded, engineered


def feature_signal(X, y, rows=50_000, seed=0):
    """How well each feature predicts the outcome on its own: AUROC for the outcome it predicts best (0.5 = not at all)."""
    n = len(y)
    idx = np.random.default_rng(seed).choice(n, size=min(n, rows), replace=False)
    yy = y[idx]
    out = {}
    for c, v in X.items():
        vv = v[idx]
        ok = np.isfinite(vv)
        best = 0.5
        if ok.sum() >= MIN_ROWS and np.unique(vv[ok]).size > 1:
            for k in range(3):
                a = auroc(yy[ok] == k, vv[ok])
                if np.isfinite(a):
                    best = max(best, a, 1 - a)
        out[c] = best
    return out


def prune_duplicates(X, y, threshold=0.90, weak=0.53, rows=20_000, seed=0):
    """Near-copies (rank correlation >= threshold) say the same thing. Keeps the one of each set that predicts the
    outcome best, or none when even that one predicts nothing (signal below `weak`). Returns the dropped columns with
    the reason, and a summary of each set."""
    sig = feature_signal(X, y)
    cols = [c for c in X if np.isfinite(X[c]).mean() >= 0.2]
    if len(cols) < 2:
        return {}, []
    n = len(y)
    idx = np.random.default_rng(seed).choice(n, size=min(n, rows), replace=False)
    corr = pd.DataFrame({c: X[c][idx] for c in cols}).rank().corr(min_periods=300)
    parent = {}

    def find(a):
        parent.setdefault(a, a)
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    vals, names = corr.to_numpy(), list(corr.columns)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            if np.isfinite(vals[i, j]) and abs(vals[i, j]) >= threshold:
                parent[find(names[i])] = find(names[j])
    sets = {}
    for f in parent:
        sets.setdefault(find(f), []).append(f)
    dropped, summary = {}, []
    for members in sets.values():
        members = sorted(members, key=lambda f: -sig[f])
        best = members[0]
        if sig[best] < weak and best not in PROTECTED:
            for f in members:
                dropped[f] = f"near-copies that all predict nothing ({', '.join(m for m in members if m != f)})"
            summary.append(dict(kept=None, dropped=members, signals={f: round(sig[f], 3) for f in members}))
            continue
        for f in members[1:]:
            if f in PROTECTED:
                continue
            rho = corr.at[f, best] if f in corr.index and best in corr.columns else np.nan
            dropped[f] = f"near-copy of {best} (rank correlation {abs(rho):.2f}), which predicts better " \
                         f"({sig[best]:.3f} vs {sig[f]:.3f})"
        summary.append(dict(kept=best, dropped=[f for f in members[1:] if f not in PROTECTED],
                            signals={f: round(sig[f], 3) for f in members}))
    return dropped, summary


def leak_check(X, y):
    """Features that predict the outcome too well alone, or whose blanks do."""
    flagged = {}
    nowhere = y == 2
    for c, v in X.items():
        ok = np.isfinite(v)
        if ok.sum() >= MIN_ROWS and np.unique(v[ok]).size > 1:
            best = max(max(a, 1 - a) for a in (auroc(y[ok] == k, v[ok]) for k in range(3)) if np.isfinite(a))
            if best > SUSPICIOUS_AUROC:
                flagged[c] = f"predicts the outcome too well on its own (AUROC {best:.3f})"
                continue
        miss = ~ok
        if c not in LEGIT_BLANKS and miss.sum() >= MIN_ROWS and ok.sum() >= MIN_ROWS:
            a, b = nowhere[miss].mean(), nowhere[ok].mean()
            if abs(a - b) > BLANK_GAP:
                flagged[c] = f"its blanks predict the outcome (goes nowhere {a:.0%} when blank vs {b:.0%} when filled)"
    return flagged


# ===================================================================== weights and splits
def repo_folds(repos, k):
    """Every repo in one fold, filling the smallest fold first so the folds have similar numbers of PRs."""
    sizes = repos.value_counts()
    totals, assign = [0] * k, {}
    for repo, n in sizes.items():
        f = int(np.argmin(totals))
        assign[repo] = f
        totals[f] += int(n)
    return repos.map(assign).astype(int), totals


def llm_status(p, llm_cols):
    """Why a PR has no LLM flags, from the miner's own rules (mine_repo._llm_skip_reason)."""
    if not llm_cols:
        return np.full(len(p), "n/a", dtype=object)
    has = np.array(p[llm_cols].notna().any(axis=1), dtype=bool)
    off = np.array(p["llm_model"].isna(), dtype=bool) if "llm_model" in p.columns else np.zeros(len(p), bool)
    trivial = _flag(p, "docs_only") | _flag(p, "test_only")
    return np.where(has, "has_flags", np.where(off, "llm_off_for_run",
                                                np.where(trivial, "skipped_trivial_diff", "skipped_other")))


def clean_reason(p):
    if "nowhere_reason" not in p.columns:
        return pd.Series([None] * len(p))
    rc = p["nowhere_reason"].astype(object).copy()
    if "closed_at" in p.columns:
        hours = (_times(p["closed_at"]) - _times(p["created_at"])).dt.total_seconds() / 3600
        quick = (p["nowhere_reason"] == "abandoned").to_numpy() & (hours < 1).fillna(False).to_numpy()
        rc[quick] = "withdrawn_or_rejected"
    return rc


# ===================================================================== run
def run(args):
    os.makedirs(args.out, exist_ok=True)
    d = frames._read(args.data)
    if not args.no_history_v2:
        clash = [c for c in HISTORY_V2 if c in d.columns]
        if clash:
            raise SystemExit("the mined table already has columns named like the history features: " + ", ".join(clash))
        d = pd.concat([d, history_v2(d)], axis=1)       # over every mined PR, before the training rows are chosen
    non_inputs = frames.non_input_columns()
    cands = [c for c in d.columns if c not in non_inputs and not c.startswith(("label_", "days_to_"))]
    groups = feature_groups(cands)
    llm_cols = [c for c in cands if groups.get(c) == "llm_flags"]
    p, steps = select_rows(d, args, llm_cols)
    p, random_filled, fill_masks = random_fill(p, RANDOM_FILL, seed=args.seed)
    if len(p) < MIN_ROWS:
        raise SystemExit(f"only {len(p)} PRs left after the row rules: too few to train on")
    counts = np.bincount(p["y"], minlength=3)
    if (counts == 0).any():
        gone = ", ".join(c for c, n in zip(CLASSES, counts) if n == 0)
        raise SystemExit(f"no {gone} PRs left after the row rules (see the counts below), so one of them removes a "
                         "whole outcome:\n" + "\n".join(f"  {n:>9,}  {r}" for r, n in steps))
    X, dropped, filled, expanded, engineered = build_features(p, args, cands, groups)
    y = p["y"].to_numpy()
    near_copies = []
    if not args.keep_duplicates:
        dup, near_copies = prune_duplicates(X, y)
        for c, why in dup.items():
            dropped[c] = why
            del X[c]
    flagged = leak_check(X, y)
    if args.drop_flagged:
        for c, why in flagged.items():
            dropped[c] = "leak check: " + why
            del X[c]

    created = _times(p["created_at"])
    n_repo = p.groupby("repo")["repo"].transform("size").to_numpy()
    weight = np.minimum(1.0, args.repo_cap / n_repo)
    exact_info = None
    if "open_snapshot" in p.columns and not args.train_recovered:
        exact = p["open_snapshot"].eq("exact").fillna(False).to_numpy(dtype=bool)
        cnt = pd.DataFrame({"r": p["repo"], "y": p["y"], "x": exact}).groupby(["r", "y"])["x"].agg(["size", "sum"])
        wx = (cnt["size"] / cnt["sum"].clip(lower=1)).reindex(pd.MultiIndex.from_arrays([p["repo"], p["y"]]))
        weight = np.where(exact, weight * np.nan_to_num(wx.to_numpy(float), nan=1.0), 0.0)
        mix = pd.crosstab(p["open_snapshot"].fillna("blank"), p["y"].map(dict(enumerate(CLASSES))), normalize="columns")
        exact_info = dict(exact_prs=int(exact.sum()), weight_zero_prs=int((~exact).sum()),
                          by_snapshot={k: int(v) for k, v in p["open_snapshot"].fillna("blank").value_counts().items()},
                          snapshot_share_by_outcome=json.loads(mix.round(4).to_json()))
    fold, fold_sizes = repo_folds(p["repo"], args.folds)
    time_split = np.where((created < pd.Timestamp(args.time_cut, tz="UTC")).fillna(False), "train", "test")
    frames_split = np.where(frames.is_test(p, by="repo", test_frac=0.2).to_numpy(), "test", "train")
    meta = [c for c in META if c in p.columns] + [c for c in EVAL_ONLY if c in p.columns and c not in X]
    labels = list(dict.fromkeys(                            # dict.fromkeys: label_bad_observed is in both lists
        [c for c in LABELS if c in p.columns] + sorted(c for c in p.columns if c.startswith("label_"))
        + [c for c in OBS_COLUMNS if c in p.columns]))
    status = llm_status(p, llm_cols)
    for c in FILL:                                          # the fixed fills happen in build_features; p keeps the blanks
        x = _num(p[c]) if c in p.columns else None
        if x is not None:
            fill_masks[c] = np.isnan(np.asarray(x, dtype=float))
    fill_names = [c for c in fill_masks if c in X]
    filled_values = ([";".join(n for n, f in zip(fill_names, row) if f)
                      for row in np.column_stack([fill_masks[c] for c in fill_names])]
                     if fill_names else [""] * len(p))
    out = pd.concat([p[meta + labels].reset_index(drop=True),
                     pd.DataFrame({"author_key": author_keys(p), "y": y, "reason_clean": clean_reason(p).to_numpy(),
                                   "llm_flags_status": status, "filled_values": filled_values,
                                   "sample_weight": weight, "fold": fold.to_numpy(),
                                   "time_split": time_split, "frames_split": frames_split}),
                     pd.DataFrame(X)], axis=1)
    features = list(X)
    csv_path = os.path.join(args.out, "train_at_open.csv")
    out.to_csv(csv_path, index=False)
    with open(os.path.join(args.out, "features.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(features) + "\n")

    quarters = pd.DataFrame({"quarter": created.dt.tz_convert(None).dt.to_period("Q").astype(str), "y": y})
    qmix = quarters.groupby("quarter")["y"].value_counts(normalize=True).unstack().reindex(columns=range(3)).fillna(0)
    qmix.columns = list(CLASSES)
    qmix.insert(0, "prs", quarters.groupby("quarter").size())
    folds = {f: sorted(p.loc[fold.to_numpy() == f, "repo"].unique().tolist()) for f in range(args.folds)}
    source = os.path.join(os.path.dirname(os.path.abspath(args.data)), "MANIFEST.json")
    src_manifest = json.load(open(source, encoding="utf-8")) if os.path.isfile(source) else None
    manifest = dict(
        built_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), script="pipelines/training/prepare_training_data.py",
        source=dict(path=os.path.abspath(args.data), sha256=_sha256(args.data),
                    build=(src_manifest or {}).get("built_at"), split_id=(src_manifest or {}).get("split_id")),
        settings=vars(args), rows=[dict(rule=a, prs=b) for a, b in steps], prs=len(out), repos=int(p["repo"].nunique()),
        outcome_mix={c: float((y == k).mean()) for k, c in enumerate(CLASSES)},
        features=features, dropped_columns=dropped, filled_columns=filled, random_filled=random_filled,
        text_columns_expanded=expanded,
        near_copy_sets=near_copies, engineered=engineered, leak_check=flagged, legit_blanks=LEGIT_BLANKS,
        sample_weight=dict(repo_cap=args.repo_cap, min=float(weight.min()), mean=float(weight.mean()),
                           exact_diffs_only=exact_info),
        history_v2=[] if args.no_history_v2 else [c for c in HISTORY_V2 if c in X],
        folds=dict(prs=fold_sizes, repos=folds),
        time_split={k: int((time_split == k).sum()) for k in ("train", "test")} | {"cut": args.time_cut},
        frames_split={k: int((frames_split == k).sum()) for k in ("train", "test")},
        llm_flags_status={k: dict(prs=int((status == k).sum()), goes_nowhere=float((y[status == k] == 2).mean()))
                          for k in pd.unique(status)},
        reason_clean_changes=int((clean_reason(p) == "withdrawn_or_rejected").sum()))
    with open(os.path.join(args.out, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, default=str)
    report(manifest, qmix, out, csv_path, args)


def report(m, qmix, out, csv_path, args):
    line = "=" * 100
    print(f"{line}\nCLEAN TRAINING TABLE\n{line}")
    print("Rows kept after each rule:")
    for r in m["rows"]:
        print(f"  {r['prs']:>9,}  {r['rule']}")
    print(f"\n{m['prs']:,} PRs from {m['repos']} repos. Outcome mix: "
          + ", ".join(f"{c} {v:.1%}" for c, v in m["outcome_mix"].items()))
    print("\nOutcome mix by quarter opened (a rising goes_nowhere share at the end means the newest PRs are still "
          "settling; raise --min-age-days if so):")
    with pd.option_context("display.width", 200, "display.float_format", "{:.3f}".format):
        print(qmix.tail(12).to_string())
    by_reason = {}
    for c, why in m["dropped_columns"].items():
        if why.startswith("near-copies that all") or why.startswith("near-copy of"):
            continue
        key = re.sub(r"\(.*?\)|\d[\d.,%]*", "", why).strip()
        by_reason.setdefault(key, []).append(c)
    print(f"\nColumns dropped ({len(m['dropped_columns'])}):")
    for why, cols in sorted(by_reason.items(), key=lambda t: -len(t[1])):
        print(f"  {len(cols):>3}  {why}: " + ", ".join(cols[:12]) + (" ..." if len(cols) > 12 else ""))
    if m["near_copy_sets"]:
        print(f"  near-copies ({len(m['near_copy_sets'])} sets; each keeps the feature that predicts the outcome best):")
        for s in m["near_copy_sets"]:
            sig = s["signals"]
            if s["kept"] is None:
                print("      dropped all (none predicts anything): " + ", ".join(f"{f} {sig[f]:.3f}" for f in s["dropped"]))
            else:
                print(f"      kept {s['kept']} {sig[s['kept']]:.3f}, dropped " + ", ".join(f"{f} {sig[f]:.3f}" for f in s["dropped"]))
    for c, f in m["filled_columns"].items():
        print(f"  filled {f['rows']:,} blanks in {c} with {f['value']}: {f['why']}")
    for c, f in m["random_filled"].items():
        print(f"  filled {f['rows']:,} blanks in {c} with random values from the same repo: {f['why']}")
    if m["text_columns_expanded"]:
        print("  text columns turned into 0/1 columns: " + ", ".join(m["text_columns_expanded"]))
    print(f"\n{len(m['features'])} features, including {len(m['engineered'])} engineered: "
          + ", ".join(e["feature"] for e in m["engineered"]))
    if m["leak_check"]:
        print("\nLeak check, look at these before training" + (" (dropped: --drop-flagged)" if args.drop_flagged else
                                                              " (kept; --drop-flagged drops them)") + ":")
        for c, why in m["leak_check"].items():
            print(f"  {c}: {why}")
    else:
        print("\nLeak check: no feature predicts the outcome too well on its own, and no blanks do.")
    w = m["sample_weight"]
    print(f"\nsample_weight: each repo counts as at most {w['repo_cap']:,} PRs (smallest weight {w['min']:.3f}).")
    if w.get("exact_diffs_only"):
        e = w["exact_diffs_only"]
        print(f"  only exact at-open diffs train: {e['exact_prs']:,} PRs carry weight, {e['weight_zero_prs']:,} get 0 "
              f"(still evaluated); by open_snapshot: {e['by_snapshot']}")
    if m.get("history_v2"):
        print(f"history (ported from Ananth): {len(m['history_v2'])} ah_*/rh* features kept: "
              + ", ".join(m["history_v2"]))
    print("fold: " + "; ".join(f"{f}: {n:,} PRs" for f, n in enumerate(m["folds"]["prs"])) + " (every repo in one fold)")
    print(f"time_split: {m['time_split']['train']:,} train / {m['time_split']['test']:,} test (cut {m['time_split']['cut']}); "
          f"frames_split: {m['frames_split']['train']:,} train / {m['frames_split']['test']:,} test")
    print(f"reason_clean: {m['reason_clean_changes']:,} 'abandoned' PRs closed within an hour relabelled withdrawn_or_rejected")
    if m["llm_flags_status"] and "n/a" not in m["llm_flags_status"]:
        print("llm_flags_status (why PRs have no LLM flags; the flags are kept, blanks stay blank):")
        for k, v in sorted(m["llm_flags_status"].items(), key=lambda t: -t[1]["prs"]):
            print(f"  {v['prs']:>9,}  {k}  (goes nowhere {v['goes_nowhere']:.0%})")
        off = m["llm_flags_status"].get("llm_off_for_run", {}).get("prs", 0)
        if off:
            print(f"  {off:,} PRs were mined with the LLM switched off: the scorer will always have flags, so those "
                  "blanks never happen in production; re-mining them with --llm would fill them")
    print(f"\n{line}\nFILES\n{line}")
    print(f"  {csv_path}  ({len(out):,} rows x {out.shape[1]} columns)")
    print(f"  {os.path.join(args.out, 'features.txt')}  (the {len(m['features'])} model inputs)")
    print(f"  {os.path.join(args.out, 'manifest.json')}  (every rule, count and dropped column)")
    print("Train on the columns in features.txt, target y, weight sample_weight; cross-validate with fold.")


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], epilog=__doc__.split("\n\n", 1)[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=os.path.join(HERE, "data", "dataset_merged.parquet"), help="the mined dataset table")
    ap.add_argument("--out", default=os.path.join(REPO_ROOT, "data", "training"),
                    help="output folder (default data/training at the repo root, which is gitignored)")
    ap.add_argument("--since", default="2023-07-01", help="first opening date to keep (the plan's window)")
    ap.add_argument("--min-age-days", type=int, default=180,
                    help="keep PRs opened at least this many days before the data was collected")
    ap.add_argument("--repo-cap", type=float, default=1500, help="most PRs a repo counts as in sample_weight")
    ap.add_argument("--folds", type=int, default=5, help="number of repo folds")
    ap.add_argument("--time-cut", default="2025-07-01", help="time_split: train before this date, test from it")
    ap.add_argument("--no-llm", action="store_true", help="leave the LLM flags out (by default they are kept, with "
                                                          "their blanks)")
    ap.add_argument("--keep-duplicates", action="store_true",
                    help="keep every near-copy feature (by default only the best of each set is kept)")
    ap.add_argument("--drop-flagged", action="store_true", help="also drop the features the leak check flags")
    ap.add_argument("--outside-only", action="store_true",
                    help="the old rows and columns: outside contributors only, by author_association (the author's "
                         "status today), with the repo signals built from it")
    ap.add_argument("--keep-body", action="store_true",
                    help="keep the features read from the PR body as it is today (dropped by default)")
    ap.add_argument("--keep-agent-flags", action="store_true",
                    help="keep ai_agent_authored, agent_name, agent_and_new_author and author_prs_merged_repo")
    ap.add_argument("--no-history-v2", action="store_true", help="leave out the ah_*/rh* history features")
    ap.add_argument("--train-recovered", action="store_true",
                    help="let PRs whose at-open diff is not exact train too (they get weight 0 by default)")
    ap.add_argument("--seed", type=int, default=0, help="seed for the random fill of mining-artifact blanks")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    args.out = os.path.normpath(args.out)
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    run(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
