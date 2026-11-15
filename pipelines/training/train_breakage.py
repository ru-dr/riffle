#!/usr/bin/env python
"""
train_breakage.py - the breakage model: if a PR merges, will it break something? P(bad | merged), predicted at open
from the outcome model's features, for label_bad (ADR 0002: revert OR ci_fail OR hotfix) and for each source alone.

    python pipelines/training/train_breakage.py --check-labels     # first: are there enough bad PRs to learn from?
    python pipelines/training/train_breakage.py                    # train, test on the test repos, save the models
    python pipelines/training/train_breakage.py --cv               # also 5-fold cross-validation by repo

Labels (ADR 0002, contracts/mined_dataset.schema.json)
  label_bad      the target: a merged PR went bad (reverted within 30 days, required CI failed on the merge commit, or a
                 hotfix-labelled PR touched its lines within 7 days). --sources picks the sources: all three (ADR
                 0002, the default) or revert hotfix (ADR 0021, proposed: CI is observed for too few PRs). Without CI
                 the label is rebuilt from its sources, here and in the file the team evaluation scores, because the
                 miner's label_bad counts CI. Merged PRs only, and PRs merged less than
                 --maturity-days (30) before the data cutoff are dropped. A source that fired makes the row bad.
                 Otherwise the row is clean, and for each source that was not observed (revert_window_closed,
                 hotfix_window_closed, ci_observed) --unobserved decides: downweight (ADR 0002: weight x 0.5 for each
                 missing source), negative (clean at full weight) or mask (drop the row). The ADR's three ablations.
  label_revert, label_hotfix, label_szz_bug, label_ci_fail
                 each source alone, on the rows eval_script.py scores it on: every "yes", and a "no" only once its
                 window has closed (revert_window_closed, hotfix_window_closed, szz_window_closed); for label_ci_fail,
                 the rows where CI was observed. SZZ is not in the target (about 0.6 precision): it is the ablation.
  A label with fewer than --min-positives bad PRs in the training repos is skipped, and the run says so.
Features   the locked outcome model's: data/training/features.txt plus the history features (train_experiments), so
           the score is known when the PR opens, like the outcome score it is combined with.
Model      LightGBM, binary, one per label: early stopping on a held-out training fold, then a refit on all the
           training repos; --seeds seeds averaged. Settings (--params): outcome (default) uses lgbm_history's tuned
           ones from data/tuning/best_params.json, as train_why.py does; conservative uses a hand-picked set for
           rare positives, to compare. Neither is tuned for breakage yet.
Testing    frames_split train/test. On the test repos: AUROC, AUPRC, Brier and its skill against three baselines (the
           training rate; the repo's own rate from its earlier merged PRs whose outcome was known by then; and whether
           the PR has CI history, which the miner kept only for recent PRs, so a label that it predicts well follows
           the date and the run warns), log loss,
           calibration, and the bad share among the 10% of PRs scored riskiest. Then the team's eval_script.py on one
           file: the outcome and why predictions (data/why_tuning/predictions_test.csv, or --outcome-preds) plus
           p_label_bad, p_label_revert, ... as P(bad | merged), which its other-labels section scores, and with the
           labels and close_reason_source, author_prs_merged_repo and title_len copied from the training table, and
           latency_ms: the milliseconds to score one PR alone (after a warm-up) through every model behind the file:
           the outcome model (data/tuning/model), the why model (data/why_tuning/model) and this run's breakage
           models with their calibration. Model time only, with the features already built; building a live PR's
           features is ingestion's part. --latency-prs test PRs are timed (default 1000, 0 = all, -1 = none).
Calibration --calibrate platt (default): bad PRs are rare and the models squeeze their probabilities together, so a
           Platt scaling (logit p' = a + b logit p) is fitted on out-of-fold predictions. For the test repos it is
           fitted on 5-fold cross-validation by repo within the training repos only, so the test repos stay unseen;
           both the calibrated model and the one before calibration are reported. For the final model it is fitted
           on the cross-validation over all repos when --cv runs (else the training-repo one is kept), and stored in
           its model card. It changes no order, so AUROC, AUPRC and lift are the same.
Tuning     tune_breakage.py searches the settings with k-fold cross-validation by repo; --params tuned uses its
           data/breakage_tuning/best_params.json.
Final      each label's model trained on all its rows, saved to <out>/model/<label>/ (boosters, features.txt,
           model_card.json, feature_importance.csv). The ranking's risk (ADR 0019) is P(merge) x P(bad | merged).

Writes to --out (default data/breakage_model at the repo root, which is gitignored). Logs to MLflow when installed.
Needs lightgbm, numpy and pandas, plus the evaluation script's own needs for the team evaluation.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
import warnings
from datetime import datetime, timezone

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import train_experiments as te  # noqa: E402  data loading, history features, the evaluation script

REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
DAY = 86400.0
LABELS = ("label_bad", "label_revert", "label_hotfix", "label_szz_bug", "label_ci_fail")
SOURCES = {"revert": ("label_revert", "revert_window_closed"), "hotfix": ("label_hotfix", "hotfix_window_closed"),
           "ci_fail": ("label_ci_fail", "ci_observed")}                  # the three sources of label_bad
RULES = {"label_revert": ("window", "revert_window_closed"), "label_hotfix": ("window", "hotfix_window_closed"),
         "label_szz_bug": ("window", "szz_window_closed"), "label_ci_fail": ("observed", "ci_observed")}
KNOWN_AFTER_DAYS = {"label_bad": 30, "label_revert": 30, "label_hotfix": 7, "label_szz_bug": 90, "label_ci_fail": 0}
OBS_COLUMNS = ("label_bad_observed", "revert_window_closed", "hotfix_window_closed", "szz_window_closed", "ci_observed")
EVAL_EXTRA = ("close_reason_source", "author_prs_merged_repo", "title_len")   # eval_script columns the why file lacks
BEST_PARAMS = os.path.join(REPO_ROOT, "data", "tuning", "best_params.json")   # lgbm_history's tuned settings
TUNED_PARAMS = os.path.join(REPO_ROOT, "data", "breakage_tuning", "best_params.json")   # from tune_breakage.py
SCORING_MODELS = (("outcome", os.path.join(REPO_ROOT, "data", "tuning", "model")),        # timed for latency_ms
                  ("why", os.path.join(REPO_ROOT, "data", "why_tuning", "model")))
CONSERVATIVE_PARAMS = dict(learning_rate=0.03, num_leaves=15, min_child_samples=100, subsample=0.8,
                           colsample_bytree=0.8, reg_lambda=5.0)
ADR_MIN_RATE = 0.02                                                     # ADR 0002: revisit if under about 2%


# ===================================================================== labels
def flag(df, col):
    """A True/False column as 1.0 / 0.0 / nan, however the CSV stored it; all nan when the column is missing."""
    if col not in df.columns:
        return np.full(len(df), np.nan)
    yes = {True, 1, 1.0, "True", "true", "1", "1.0"}
    no = {False, 0, 0.0, "False", "false", "0", "0.0"}
    return np.array([1.0 if (isinstance(v, (bool, int, float, str, np.bool_, np.number)) and v in yes) else
                     0.0 if (isinstance(v, (bool, int, float, str, np.bool_, np.number)) and v in no) else np.nan
                     for v in df[col].to_numpy(object)])


def label_setup(df, label, args):
    """For one label: y (1 bad, 0 clean, nan unknown), a weight multiplier, the rows it trains and tests on, and for
    label_bad how many of its sources were not observed."""
    n = len(df)
    merged = (df["outcome_3class"] != "goes_nowhere").to_numpy()
    mult = np.ones(n)
    if label == "label_bad":
        chosen = [SOURCES[k] for k in args.sources]
        fired = (flag(df, "label_bad") == 1) if set(args.sources) == set(SOURCES) else np.zeros(n, bool)
        missing = np.zeros(n, int)
        for lab, obs in chosen:
            f, o = flag(df, lab), flag(df, obs)
            fired |= f == 1
            missing += ~((o == 1) | (f == 1))
        age = (df["_observed_s"].to_numpy(float) - df["_closed_s"].to_numpy(float)) / DAY
        mature = ~np.isfinite(age) | (age >= args.maturity_days)
        ok = merged & mature
        neg = ok & ~fired
        if args.unobserved == "downweight":
            mult[neg] = 0.5 ** missing[neg]
        elif args.unobserved == "mask":
            ok &= fired | (missing == 0)
        return np.where(ok, fired.astype(float), np.nan), mult, ok, missing
    kind, obs = RULES[label]
    f, o = flag(df, label), flag(df, obs)
    ok = merged & np.isfinite(f) & ((o == 1) if kind == "observed" else ((f == 1) | (o == 1)))
    return np.where(ok, f, np.nan), mult, ok, None


def repo_rate(df, y, ok, prior, known_days, k=30.0):
    """Baseline: each PR gets its repo's bad rate among the earlier merged PRs whose outcome was known before it opened
    (merged at least known_days earlier), shrunk toward the training rate with k pseudo-counts."""
    created = df["_created_s"].to_numpy(float)
    known_at = df["_closed_s"].to_numpy(float) + known_days * DAY
    out = np.full(len(df), prior)
    for _, idx in df.groupby("repo", sort=False).indices.items():
        e = idx[ok[idx] & np.isfinite(known_at[idx])]
        if not len(e):
            continue
        order = np.argsort(known_at[e], kind="stable")
        kt = known_at[e][order]
        cum = np.concatenate([[0.0], np.cumsum(y[e][order])])
        j = np.searchsorted(kt, np.where(np.isfinite(created[idx]), created[idx], -np.inf), side="left")
        out[idx] = (cum[j] + k * prior) / (j + k)
    return out


# ===================================================================== --check-labels
def check_labels(df, args):
    """Per repo: merged PRs, bad PRs by source, and how often each source was observed (ADR 0002's first check)."""
    merged = (df["outcome_3class"] != "goes_nowhere").to_numpy()
    y, _, ok, missing = label_setup(df, "label_bad", args)
    src = {name: (flag(df, lab), flag(df, obs)) for name, (lab, obs) in SOURCES.items()}
    szz, szz_obs = flag(df, "label_szz_bug"), flag(df, "szz_window_closed")
    bad_obs = flag(df, "label_bad_observed")
    rows = []
    for repo, idx in df.groupby("repo", sort=True).indices.items():
        m = idx[merged[idx]]
        k = idx[ok[idx]]
        split = df["frames_split"].iloc[idx].mode().iloc[0] if "frames_split" in df.columns else ""
        r = dict(repo=repo, split=split, merged=len(m), usable=len(k), bad=int(np.nansum(y[k])),
                 bad_rate=float(np.nanmean(y[k])) if len(k) else np.nan)
        for name, (f, o) in src.items():
            r[name] = int(np.nansum(f[m] == 1))
            r[f"{name}_observed"] = float(np.mean((o[m] == 1) | (f[m] == 1))) if len(m) else np.nan
        r["szz"] = int(np.nansum(szz[m] == 1))
        r["szz_observed"] = float(np.mean((szz_obs[m] == 1) | (szz[m] == 1))) if len(m) else np.nan
        r["bad_observed"] = float(np.mean(bad_obs[m] == 1)) if len(m) else np.nan
        rows.append(r)
    t = pd.DataFrame(rows)
    os.makedirs(args.out, exist_ok=True)
    t.to_csv(os.path.join(args.out, "label_check.csv"), index=False)
    line = "=" * 100
    print(f"{line}\nBREAKAGE LABELS: are there enough bad PRs to learn from? (ADR 0002)\n{line}")
    print(f"label_bad = {' OR '.join(args.sources)}, merged PRs merged at least {args.maturity_days} days before the "
          f"cutoff; unobserved sources: {args.unobserved}.\n")
    show = t[["repo", "split", "merged", "usable", "bad", "bad_rate", "revert", "hotfix", "ci_fail", "szz",
              "revert_observed", "hotfix_observed", "ci_fail_observed", "szz_observed", "bad_observed"]]
    with pd.option_context("display.width", 220, "display.max_columns", 30, "display.max_rows", 200,
                           "display.float_format", "{:.3f}".format):
        print(show.to_string(index=False))
    print()
    for split in ("train", "test"):
        s = t[t["split"] == split]
        if len(s):
            print(f"{split:5s}: {int(s['usable'].sum()):,} usable merged PRs in {len(s)} repos, {int(s['bad'].sum()):,} bad "
                  f"({s['bad'].sum() / max(s['usable'].sum(), 1):.2%}); sources fired: revert {int(s['revert'].sum())}, "
                  f"hotfix {int(s['hotfix'].sum())}, CI {int(s['ci_fail'].sum())}, SZZ (ablation) {int(s['szz'].sum())}")
    low = t[(t["usable"] > 0) & (t["bad_rate"] < ADR_MIN_RATE)]
    print(f"\n{len(low)} of {len(t)} repos have a bad rate under {ADR_MIN_RATE:.0%}"
          + (": " + ", ".join(low["repo"]) if len(low) else "") + ". ADR 0002 says to revisit the target if this is "
          "most repos.")
    ci = float(np.average(t["ci_fail_observed"].fillna(0), weights=t["merged"].clip(lower=1)))
    if ci < 0.2 and "ci_fail" in args.sources:
        print(f"CI was observed for {ci:.0%} of merged PRs, so for most of them label_bad can only come from a revert "
              "or a hotfix; with --unobserved downweight every clean PR is weighted down for it, which raises every "
              "predicted rate.")
    usable = ok & merged
    mined = flag(df, "label_bad")
    from_sources = np.zeros(len(df), bool)
    for lab, _ in SOURCES.values():
        from_sources |= flag(df, lab) == 1
    both_known = usable & np.isfinite(mined)
    differ = both_known & ((mined == 1) != from_sources)
    if set(args.sources) != set(SOURCES):
        print(f"label_bad is rebuilt from {' OR '.join(args.sources)}: {int(np.nansum(y[usable])):,} bad usable PRs "
              f"(the miner's label_bad, which also counts CI, says {int(np.sum(mined[usable] == 1)):,}).")
    print(f"The miner's label_bad says bad for {int(np.sum(mined[usable] == 1)):,} usable PRs; revert OR hotfix OR "
          f"ci_fail from its sources says bad for {int(from_sources[usable].sum()):,}. They disagree on "
          f"{int(differ.sum()):,} PRs" + (": label_bad is not exactly ADR 0002's definition, check labels.py before "
                                         "training (this run counts a PR bad when either says so)." if differ.any()
                                         else ", so label_bad matches ADR 0002's definition."))
    tr = t[t["split"] == "train"]
    print("\nWould train (at least --min-positives = %d bad PRs in the training repos):" % args.min_positives)
    for lab in LABELS:
        yy, _, okk, _ = label_setup(df, lab, args)
        tr_rows = okk & (df["frames_split"] == "train").to_numpy()
        pos = int(np.nansum(yy[tr_rows]))
        print(f"  {lab:14s} {pos:6,d} bad of {int(tr_rows.sum()):,} rows  -> "
              + ("yes" if pos >= args.min_positives and pos < tr_rows.sum() else "no, skipped"))
    print(f"\nwrote {os.path.join(args.out, 'label_check.csv')}")
    return t


# ===================================================================== model
def model_params(args):
    """The LightGBM settings and where they came from."""
    if args.params == "tuned":
        if os.path.isfile(TUNED_PARAMS):
            with open(TUNED_PARAMS, encoding="utf-8") as fh:
                return dict(json.load(fh)["params"]), "tuned for breakage (data/breakage_tuning/best_params.json)"
        print(f"  note: {TUNED_PARAMS} not found (run tune_breakage.py), so lgbm_history's settings are used")
    if args.params in ("outcome", "tuned"):
        if os.path.isfile(BEST_PARAMS):
            with open(BEST_PARAMS, encoding="utf-8") as fh:
                return dict(json.load(fh)["params"]), "lgbm_history's tuned settings (data/tuning/best_params.json)"
        print(f"  note: {BEST_PARAMS} not found, so the conservative settings are used")
    return dict(CONSERVATIVE_PARAMS), "conservative hand-picked settings"


def _lgb():
    try:
        import lightgbm as lgb
        return lgb
    except ImportError:
        raise SystemExit("train_breakage.py needs lightgbm: pip install lightgbm")


def fit_stop_refit(X, y, w, fit, val, seed, params, scale=1.1, max_rounds=5000, fixed_rounds=300):
    """Early stopping on the val rows (when they hold both answers), then a refit on fit + val."""
    lgb = _lgb()
    base = dict(objective="binary", subsample_freq=1, random_state=seed, verbose=-1, **params)
    rounds = fixed_rounds
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if val.any() and np.unique(y[val]).size == 2:
            m = lgb.LGBMClassifier(n_estimators=max_rounds, **base)
            m.fit(X[fit], y[fit], sample_weight=w[fit], eval_set=[(X[val], y[val])], eval_sample_weight=[w[val]],
                  callbacks=[lgb.early_stopping(100, verbose=False)])
            rounds = int(m.best_iteration_ or max_rounds)
        both = fit | val
        final = lgb.LGBMClassifier(n_estimators=max(50, int(rounds * scale)), **base)
        final.fit(X[both], y[both], sample_weight=w[both])
    return final, rounds


def p_bad(model, X):
    raw = model.predict_proba(X)
    cls = list(model.classes_)
    return raw[:, cls.index(1)] if 1 in cls else np.zeros(len(X))


def _logit(p):
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def platt_fit(p, y, w=None, ridge=1e-4):
    """Platt scaling: (a, b) of logit P(bad) = a + b logit p, by weighted Newton-Raphson on the out-of-fold p."""
    x, y = _logit(p), np.asarray(y, float)
    w = np.ones(len(y)) if w is None else np.asarray(w, float)
    X = np.column_stack([np.ones_like(x), x])
    beta = np.array([0.0, 1.0])
    for _ in range(100):
        mu = 1 / (1 + np.exp(-np.clip(X @ beta, -35, 35)))
        H = X.T @ (X * (w * mu * (1 - mu))[:, None]) + ridge * np.eye(2)
        step = np.linalg.solve(H, X.T @ (w * (y - mu)) - ridge * (beta - [0.0, 1.0]))
        beta = beta + step
        if np.max(np.abs(step)) < 1e-9:
            break
    return float(beta[0]), float(beta[1])


def platt_apply(p, a, b):
    return 1 / (1 + np.exp(-np.clip(a + b * _logit(p), -35, 35)))


def oof_within(X, yi, w, ok, rows, folds, seed, params):
    """Out-of-fold predictions for `rows`: each fold of them is predicted by a model trained on their other folds
    (early stopping on the next fold), so no prediction has seen its own repo."""
    ids = sorted(np.unique(folds[rows & ok]))
    oof = np.full(len(yi), np.nan)
    for i, f in enumerate(ids):
        hold = rows & ok & (folds == f)
        val = rows & ok & (folds == ids[(i + 1) % len(ids)])
        fit = rows & ok & ~hold & ~val
        if not fit.any() or np.unique(yi[fit]).size < 2:
            continue
        m, _ = fit_stop_refit(X, yi, w, fit, val, seed, params)
        oof[hold] = p_bad(m, X[hold])
    return oof


def calibrator(oof, y, w, ok, rows, notes, what):
    """(a, b) fitted on the out-of-fold predictions of `rows`, or None (with a note) when it cannot be trusted."""
    m = rows & ok & np.isfinite(oof)
    if m.sum() < 100 or np.unique(y[m]).size < 2:
        notes.append(f"{what}: too few out-of-fold predictions to calibrate, so it is not calibrated")
        return None
    a, b = platt_fit(oof[m], y[m], w[m])
    if not b > 0:
        notes.append(f"{what}: the calibration slope came out {b:.2f} (it would reverse the order), so it is not "
                     "calibrated")
        return None
    return a, b


def scores(ev, y, p):
    yb = y.astype(float)
    pc = np.clip(p, 1e-15, 1 - 1e-15)
    off, slope = ev.calibration_offset_slope(yb, p)
    top = p >= np.quantile(p, 0.9)
    share = float(yb.mean())
    return dict(prs=int(len(y)), bad=int(yb.sum()), share_bad=share, auroc=ev.auroc_binary(yb == 1, p),
                auprc=ev.average_precision(yb == 1, p), brier=float(np.mean((p - yb) ** 2)),
                log_loss=float(-np.mean(yb * np.log(pc) + (1 - yb) * np.log(1 - pc))),
                calibration_error=ev.ece_binary(yb, p, 15), slope=slope, offset=off,
                bad_share_top10pct=float(yb[top].mean()) if top.any() else np.nan,
                lift_top10pct=float(yb[top].mean() / share) if top.any() and share > 0 else np.nan)


# ===================================================================== run
def load(args):
    df = pd.read_csv(args.data, low_memory=False)
    with open(args.features, encoding="utf-8") as fh:
        feats = [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]
    need = ["repo", "pr_number", "y", "fold", "sample_weight", "created_at", "closed_at", "frames_split", "outcome_3class"]
    miss = [c for c in need + feats if c not in df.columns]
    if miss:
        raise SystemExit("the training CSV is missing: " + ", ".join(miss[:10]))
    if not any(c in df.columns for c in LABELS):
        raise SystemExit("the training CSV has none of the breakage labels (" + ", ".join(LABELS) + ")")
    extra = {"_created_s": te._seconds(df["created_at"]), "_closed_s": te._seconds(df["closed_at"]),
             "_observed_s": te._seconds(df["observed_through"]) if "observed_through" in df.columns
             else np.full(len(df), np.nan)}
    return pd.concat([df, pd.DataFrame(extra, index=df.index)], axis=1), feats


def run(args):
    os.makedirs(args.out, exist_ok=True)
    df, feats = load(args)
    if args.check_labels:
        return check_labels(df, args)
    H, _ = te.history_features(df, seed=args.seed)
    names = feats + list(H.columns)
    X = np.hstack([df[feats].apply(pd.to_numeric, errors="coerce").to_numpy(float), H.to_numpy(float)])
    w0 = df["sample_weight"].to_numpy(float)
    tr = (df["frames_split"] == "train").to_numpy()
    tst = (df["frames_split"] == "test").to_numpy()
    val_fold = df.loc[tr, "fold"].value_counts().idxmax()
    val = tr & (df["fold"] == val_fold).to_numpy()
    ev = te.load_eval_module()
    seeds = [args.seed + s for s in range(args.seeds)]
    params, params_from = model_params(args)
    line = "=" * 100
    print(f"{line}\nTHE BREAKAGE MODEL: if a PR merges, will it break something?\n{line}")
    print(f"Features: {len(names)} (the outcome model's). Train repos {df.loc[tr, 'repo'].nunique()}, test repos "
          f"{df.loc[tst, 'repo'].nunique()}. Unobserved label_bad sources: {args.unobserved}. Seeds: {len(seeds)}.")
    print(f"Settings: {params_from}.")

    results, skipped, preds, rounds_by, gains_by, setups, rebuilt = [], {}, {}, {}, {}, {}, {}
    models_by = {}
    cal_notes, cal_test = [], {}
    folds_all = df["fold"].to_numpy()
    merged = (df["outcome_3class"] != "goes_nowhere").to_numpy()
    ci_hist = flag(df, "ci_observed") == 1
    t0 = time.time()
    for lab in args.labels:
        if lab not in df.columns and lab != "label_bad":
            skipped[lab] = "not in the training CSV"
            continue
        y, mult, ok, missing = label_setup(df, lab, args)
        if lab == "label_bad" and set(args.sources) != set(SOURCES):
            rebuilt["label_bad"] = np.where(ok, y, np.nan)
            rebuilt["label_bad_observed"] = np.where(merged, (ok & ((missing == 0) | (y == 1))).astype(float), np.nan)
        pos_tr = int(np.nansum(y[ok & tr]))
        if pos_tr < args.min_positives or pos_tr >= int((ok & tr).sum()):
            skipped[lab] = f"{pos_tr} bad PRs in the training repos (needs {args.min_positives})"
            continue
        w = w0 * mult
        yi = np.where(ok, np.nan_to_num(y), 0).astype(int)
        P, rounds, gains = [], [], []
        models_by[lab] = []
        for s in seeds:
            m, r = fit_stop_refit(X, yi, w, tr & ~val & ok, val & ok, s, params)
            models_by[lab].append(m)
            P.append(p_bad(m, X))
            rounds.append(r)
            gains.append(m.booster_.feature_importance("gain"))
        p_raw = np.mean(P, axis=0)
        p = p_raw
        if args.calibrate == "platt":
            oof = oof_within(X, yi, w, ok, tr, folds_all, args.seed, params)
            cal_test[lab] = calibrator(oof, y, w, ok, tr, cal_notes, f"{lab} (training repos)")
            if cal_test[lab] is not None:
                p = platt_apply(p_raw, *cal_test[lab])
        preds[lab], rounds_by[lab], gains_by[lab], setups[lab] = p, rounds, gains, (y, mult, ok)
        test_rows = ok & tst
        prior = float(np.average(y[ok & tr], weights=w[ok & tr]))
        base_repo = repo_rate(df, np.nan_to_num(y), ok, prior, KNOWN_AFTER_DAYS[lab])
        ci_rate = {}
        for g in (True, False):
            rows_g = ok & tr & (ci_hist == g)
            ci_rate[g] = float(np.average(y[rows_g], weights=w[rows_g])) if rows_g.any() and w[rows_g].sum() > 0 else prior
        base_ci = np.where(ci_hist, ci_rate[True], ci_rate[False])
        yt = y[test_rows]
        if test_rows.sum() < 20 or np.unique(yt).size < 2:
            results.append(dict(label=lab, predictor="model", note="too few test PRs, or only one answer"))
        else:
            raw_row = (("model before calibration", p_raw[test_rows]),) if p is not p_raw else ()
            for name, pp in (("model", p[test_rows]), *raw_row,
                             ("baseline: training rate", np.full(int(test_rows.sum()), prior)),
                             ("baseline: repo's earlier rate", base_repo[test_rows]),
                             ("baseline: has CI history", base_ci[test_rows])):
                results.append(dict(label=lab, predictor=name, **scores(ev, yt, pp)))
        print(f"  {lab}: {pos_tr:,} bad PRs to learn from; trained {len(seeds)} seeds ({time.time() - t0:.0f}s)",
              flush=True)
    for lab, why in skipped.items():
        print(f"  {lab}: skipped, {why}")
    if not preds:
        raise SystemExit("no label had enough bad PRs to train on: run --check-labels to see why")
    comp = pd.DataFrame(results)
    for k in ("brier", "log_loss"):
        if k in comp.columns:
            ref = comp[comp["predictor"].str.startswith("baseline")].groupby("label")[k].min()
            comp[f"{k}_skill_vs_best_baseline"] = 1 - comp[k] / comp["label"].map(ref)
    comp.to_csv(os.path.join(args.out, "breakage_vs_baselines.csv"), index=False)

    # ---- the team's evaluation: outcome + why + breakage in one file
    summ = None
    if not args.no_eval:
        summ = team_eval(args, df, tr, tst, preds, names, ev, rebuilt, X, models_by, cal_test)

    # ---- optional cross-validation by repo
    cv, oof_first = None, {}
    if args.cv:
        res, oof_first = [], {}
        for rep in range(args.repeats):
            folds = df["fold"].to_numpy() if rep == 0 else te.repo_folds(df, df["fold"].nunique(), args.seed + 100 + rep)
            ids = sorted(np.unique(folds))
            for lab, (y, mult, ok) in setups.items():
                yi = np.where(ok, np.nan_to_num(y), 0).astype(int)
                oof = np.full(len(df), np.nan)
                for i, f in enumerate(ids):
                    te_f, vf = folds == f, folds == ids[(i + 1) % len(ids)]
                    m, _ = fit_stop_refit(X, yi, w0 * mult, ~te_f & ~vf & ok, vf & ok, args.seed + rep, params)
                    oof[te_f & ok] = p_bad(m, X[te_f & ok])
                good = ok & np.isfinite(oof)
                if rep == 0:
                    oof_first[lab] = oof
                if np.unique(y[good]).size == 2:
                    res.append(dict(label=lab, run=rep + 1, **scores(ev, y[good], oof[good])))
            print(f"  cross-validation {rep + 1} of {args.repeats} done", flush=True)
        cv = pd.DataFrame(res)
        cv.to_csv(os.path.join(args.out, "breakage_cv.csv"), index=False)

    # ---- final models on every row of each label
    mroot = os.path.join(args.out, "model")
    cards = {}
    lgb = _lgb()
    for lab, (y, mult, ok) in setups.items():
        cal_final, cal_from = cal_test.get(lab), "out-of-fold predictions within the training repos"
        if args.calibrate == "platt" and lab in oof_first:
            c = calibrator(oof_first[lab], y, w0 * mult, ok, np.ones(len(df), bool), cal_notes, f"{lab} (all repos)")
            if c is not None:
                cal_final, cal_from = c, "out-of-fold predictions of the cross-validation over all repos"
        mdir = os.path.join(mroot, lab)
        os.makedirs(mdir, exist_ok=True)
        n_final = max(50, int(np.median(rounds_by[lab]) * 1.2))
        yi = np.where(ok, np.nan_to_num(y), 0).astype(int)
        fg = []
        for s in seeds:
            m = lgb.LGBMClassifier(objective="binary", n_estimators=n_final, subsample_freq=1, random_state=s, verbose=-1,
                                   **params)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                m.fit(X[ok], yi[ok], sample_weight=(w0 * mult)[ok])
            m.booster_.save_model(os.path.join(mdir, f"model_seed{s}.txt"))
            fg.append(m.booster_.feature_importance("gain"))
        g = np.mean(fg, axis=0)
        imp = pd.DataFrame({"feature": names, "gain_share": g / max(g.sum(), 1e-12)}).sort_values("gain_share",
                                                                                                    ascending=False)
        imp.to_csv(os.path.join(mdir, "feature_importance.csv"), index=False)
        with open(os.path.join(mdir, "features.txt"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(names) + "\n")
        test = comp[(comp["label"] == lab) & (comp["predictor"] == "model")]
        card = dict(model=f"breakage: {lab} (LightGBM binary, the outcome model's features)",
                    created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    task=f"P({lab} | merged), predicted at open",
                    how_to_score=("average predict(features) of the model_seed*.txt boosters, features in features.txt "
                                  "order (the outcome model's at-open features, then train_experiments history "
                                  "features), then the calibration below when there is one. Risk for ranking "
                                  "(ADR 0019): P(merge) x this."),
                    rows=("%s; merged PRs merged at least %d days before the cutoff; unobserved sources: %s"
                          % (" OR ".join(args.sources), args.maturity_days, args.unobserved)) if lab == "label_bad" else
                         f"merged PRs; every yes, and a no once {RULES[lab][1]} is true (as eval_script.py scores it)",
                    calibration=None if cal_final is None else dict(method="platt", a=cal_final[0], b=cal_final[1],
                                                                    fitted_on=cal_from,
                                                                    apply="p = 1 / (1 + exp(-(a + b * logit(p_raw))))"),
                    params=params, params_from=params_from, boosting_rounds=n_final, seeds=seeds,
                    trained_on_prs=int(ok.sum()),
                    bad_prs=int(np.nansum(y[ok])),
                    test=test.drop(columns=["label", "predictor"]).iloc[0].to_dict() if len(test) else None,
                    cv=None if cv is None or cv.empty else cv[cv["label"] == lab].drop(columns=["label", "run"]).mean(
                        numeric_only=True).to_dict())
        with open(os.path.join(mdir, "model_card.json"), "w", encoding="utf-8") as fh:
            json.dump(card, fh, indent=2, default=str)
        cards[lab] = card
    with open(os.path.join(mroot, "model_card.json"), "w", encoding="utf-8") as fh:
        json.dump(dict(labels=sorted(cards), skipped=skipped, label_bad_sources=list(args.sources),
                       unobserved=args.unobserved, seeds=seeds,
                       params_from=params_from, created_at=datetime.now(timezone.utc).isoformat(timespec="seconds")),
                  fh, indent=2)
    log_mlflow(args, cards, mroot, params, params_from)
    report(args, comp, summ, cv, skipped, mroot, cal_notes, cal_test)
    return comp


def scoring_latency(args, df, X, names, rows, models_by, cal_by):
    """Milliseconds to score each PR of `rows` alone through every model behind the predictions file, the way the
    scorer would: the outcome and why boosters from disk and this run's breakage boosters with their calibration.
    Returns (ms per row, nan where not timed; the stages timed; notes)."""
    lgb = _lgb()
    notes = []
    F = pd.DataFrame(X, columns=names, index=df.index)
    stages = []
    for name, mdir in SCORING_MODELS:
        files = sorted(glob.glob(os.path.join(mdir, "model_seed*.txt")))
        feat_file = os.path.join(mdir, "features.txt")
        if not files or not os.path.isfile(feat_file):
            notes.append(f"the {name} model is not in {mdir}, so it is not timed")
            continue
        with open(feat_file, encoding="utf-8") as fh:
            cols = [ln.strip() for ln in fh if ln.strip()]
        missing = [c for c in cols if c not in F.columns]
        if missing and name == "why":
            try:
                import train_why as tw
                card = os.path.join(mdir, "model_card.json")
                mode = "cleaned"
                if os.path.isfile(card):
                    with open(card, encoding="utf-8") as fh:
                        mode = json.load(fh).get("labels", "cleaned")
                codes = tw.reason_codes(df, mode)[0]
                RH = tw.reason_history(df, codes, seed=args.seed)
                F = pd.concat([F, RH.set_index(df.index)], axis=1)
                missing = [c for c in cols if c not in F.columns]
            except Exception as e:
                notes.append(f"the why model's reason-history features could not be built ({type(e).__name__}: {e})")
        if missing:
            notes.append(f"{len(missing)} of the {name} model's features are not available here and are passed empty, "
                         "which barely changes the time")
        stages.append((f"{name} ({len(files)} boosters)", [lgb.Booster(model_file=f) for f in files],
                       F.reindex(columns=cols).to_numpy(float), None))
    for lab, models in models_by.items():
        stages.append((f"breakage {lab} ({len(models)} boosters)", [m.booster_ for m in models], X, cal_by.get(lab)))

    def score_one(i):
        for _, boosters, Xm, cal in stages:
            p = np.mean([b.predict(Xm[i:i + 1]) for b in boosters], axis=0)
            if cal is not None:
                p = platt_apply(p, *cal)

    idx = np.flatnonzero(rows)
    ms = np.full(len(df), np.nan)
    if not len(idx) or not stages:
        return ms, [s[0] for s in stages], notes
    if 0 < args.latency_prs < len(idx):
        idx = np.sort(np.random.default_rng(args.seed).choice(idx, args.latency_prs, replace=False))
    for i in idx[:20]:                                     # warm-up: first calls pay one-off costs
        score_one(i)
    for i in idx:
        t0 = time.perf_counter()
        score_one(i)
        ms[i] = (time.perf_counter() - t0) * 1000.0
    return ms, [s[0] for s in stages], notes


def _as_bool(v):
    return pd.Series(v).map({1.0: True, 0.0: False}).astype(object).where(pd.notna(pd.Series(v)), None).to_numpy()


def team_eval(args, df, tr, tst, preds, names, ev, rebuilt=None, X=None, models_by=None, cal_by=None):
    """One predictions file for eval_script.py: the outcome and why predictions plus p_label_* from this run. When
    label_bad was rebuilt from fewer sources, the file's label_bad and label_bad_observed are rebuilt the same way, so
    the model is scored against the label it learned."""
    rebuilt = rebuilt or {}
    base_path = args.outcome_preds or next((p for p in (os.path.join(REPO_ROOT, "data", "why_tuning", "predictions_test.csv"),
                                                         os.path.join(REPO_ROOT, "data", "tuning", "predictions_test.csv"))
                                            if os.path.isfile(p)), None)
    if not base_path or not os.path.isfile(base_path):
        print("  (no outcome predictions file found, so the team evaluation is skipped: pass --outcome-preds)")
        return None
    print(f"  running the team's evaluation on the test repos (outcome and why from {os.path.relpath(base_path, REPO_ROOT)}) ...",
          flush=True)
    keys = ["repo", "pr_number"]
    add = df.loc[tst, keys + [c for c in (*LABELS, *OBS_COLUMNS, *EVAL_EXTRA) if c in df.columns]].copy()
    for lab, p in preds.items():
        add[f"p_{lab}"] = p[tst]
    for col, v in rebuilt.items():
        add[col] = _as_bool(v[tst])
    if rebuilt:
        print(f"  note: label_bad and label_bad_observed in the evaluation file are rebuilt from "
              f"{' OR '.join(args.sources)} (the miner's label_bad also counts CI)")
    if args.latency_prs >= 0 and X is not None:
        ms, timed, lat_notes = scoring_latency(args, df, X, names, tst, models_by or {}, cal_by or {})
        add["latency_ms"] = np.round(ms[tst], 3)
        v = ms[np.isfinite(ms)]
        if v.size:
            print(f"  latency_ms: {v.size:,} test PRs scored one at a time through {', '.join(timed)}: "
                  f"median {np.median(v):.1f} ms, 95% within {np.percentile(v, 95):.1f} ms, "
                  f"99% within {np.percentile(v, 99):.1f} ms (model time; features already built)")
        for n in lat_notes:
            print("  note (latency): " + n)
    base = pd.read_csv(base_path, low_memory=False)
    base = base.drop(columns=[c for c in add.columns if c in base.columns and c not in keys])
    both = base.merge(add, on=keys, how="left")
    unmatched = int(both[f"p_{next(iter(preds))}"].isna().sum())
    if unmatched:
        print(f"  note: {unmatched:,} rows of the outcome predictions have no breakage prediction (not test rows here)")
    pred_path = os.path.join(args.out, "predictions_test.csv")
    both.to_csv(pred_path, index=False)
    train_csv = os.path.join(args.out, "train_rows_for_eval.csv")
    cols = ["repo", "pr_number", "outcome_3class", "created_at", "closed_at"]
    tr_rows = df.loc[tr, cols + [c for c in (*LABELS, *OBS_COLUMNS) if c in df.columns]].copy()
    for col, v in rebuilt.items():
        tr_rows[col] = _as_bool(v[tr])
    tr_rows.to_csv(train_csv, index=False)
    feat_file = os.path.join(args.out, "features_used.txt")
    with open(feat_file, "w", encoding="utf-8") as fh:
        fh.write("\n".join(names) + "\n")
    return te.run_eval(ev, pred_path, "lgbm_history + why + breakage", os.path.join(args.out, "eval_test"), train_csv,
                       feat_file, args.boot)


def log_mlflow(args, cards, mroot, params, params_from):
    try:
        import mlflow
    except ImportError:
        return
    try:
        mlflow.set_experiment(args.mlflow_experiment)
        with mlflow.start_run(run_name="breakage_model"):
            mlflow.log_params({**params, "params_from": args.params, "sources": ",".join(args.sources),
                               "unobserved": args.unobserved,
                               "maturity_days": args.maturity_days, "labels": ",".join(sorted(cards))})
            for lab, card in cards.items():
                for k, v in (card.get("test") or {}).items():
                    if isinstance(v, (int, float)) and np.isfinite(v):
                        mlflow.log_metric(f"test_{lab}_{k}", float(v))
            mlflow.log_artifacts(mroot, artifact_path="model")
    except Exception as e:                                    # tracking must never stop the run
        print(f"  (MLflow logging failed: {type(e).__name__}: {e})")


def report(args, comp, summ, cv, skipped, mroot, cal_notes=(), cal_test=None):
    line = "=" * 100
    print(f"\n{line}\nON THE TEST REPOS (P(bad | merged); AUPRC is worth comparing with share_bad, its value for a random "
          f"order)\n{line}")
    cols = [c for c in ("label", "predictor", "prs", "bad", "share_bad", "auroc", "auprc", "brier",
                        "brier_skill_vs_best_baseline", "log_loss", "calibration_error", "slope", "offset",
                        "bad_share_top10pct", "lift_top10pct", "note") if c in comp.columns]
    with pd.option_context("display.width", 240, "display.max_columns", 30, "display.float_format", "{:.4f}".format):
        print(comp[cols].to_string(index=False))
    print("lift_top10pct: how many times more often the 10% of PRs the model calls riskiest went bad than PRs on average. "
          "slope < 1: too extreme; offset < 0: predicted rates too high.")
    for lab, c in (cal_test or {}).items():
        if c is not None:
            print(f"calibration of {lab}: logit p' = {c[0]:+.3f} + {c[1]:.3f} logit p (Platt, fitted on out-of-fold "
                  "predictions within the training repos); 'model' is calibrated, AUROC/AUPRC/lift are unchanged by it")
    for n in cal_notes:
        print("note: " + n)
    if "auroc" in comp.columns:
        for _, r in comp[comp["predictor"] == "baseline: has CI history"].iterrows():
            if np.isfinite(r["auroc"]) and r["auroc"] > 0.65:
                print(f"WARNING {r['label']}: whether the PR has CI history predicts it with AUROC {r['auroc']:.2f}. CI was "
                      "kept only for recent PRs, so this label follows the date and the model's score partly measures "
                      "how recent a PR is.")
    if summ and summ.get("other_labels"):
        t = pd.DataFrame(summ["other_labels"])
        t = t[t["group"] == "after merge"]
        if len(t):
            print(f"\nThe team's evaluation (repo-averaged, its other-labels section; full report in "
                  f"{os.path.join(args.out, 'eval_test')}):")
            keep = [c for c in ("label", "prs", "repos", "share_yes", "brier", "skill", "skill_lo", "skill_hi", "auroc",
                                "auprc", "calibration_error", "caught_at_20pct_effort", "note") if c in t.columns]
            with pd.option_context("display.width", 240, "display.max_columns", 30, "display.float_format",
                                   "{:.4f}".format):
                print(t[keep].to_string(index=False))
    if cv is not None and not cv.empty:
        print("\nCross-validation by repo (mean over runs):")
        print(cv.groupby("label")[["auroc", "auprc", "share_bad", "brier", "lift_top10pct"]].mean().round(4).to_string())
    for lab, why in skipped.items():
        print(f"skipped {lab}: {why}")
    print(f"\nModels in {mroot}/<label>/; comparison in {os.path.join(args.out, 'breakage_vs_baselines.csv')}")


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], epilog=__doc__.split("\n\n", 1)[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=os.path.join(REPO_ROOT, "data", "training", "train_at_open.csv"))
    ap.add_argument("--features", default=os.path.join(REPO_ROOT, "data", "training", "features.txt"))
    ap.add_argument("--out", default=os.path.join(REPO_ROOT, "data", "breakage_model"))
    ap.add_argument("--labels", nargs="+", default=list(LABELS), choices=LABELS)
    ap.add_argument("--sources", nargs="+", default=list(SOURCES), choices=list(SOURCES),
                    help="what makes up label_bad: all three (ADR 0002) or revert hotfix (ADR 0021, proposed)")
    ap.add_argument("--unobserved", default="downweight", choices=["downweight", "negative", "mask"],
                    help="label_bad rows with unobserved sources (ADR 0002 and its ablations)")
    ap.add_argument("--params", default="outcome", choices=["outcome", "conservative", "tuned"],
                    help="LightGBM settings: lgbm_history's tuned ones (default), the conservative set, or the ones "
                         "tune_breakage.py found for breakage")
    ap.add_argument("--calibrate", default="platt", choices=["platt", "none"],
                    help="Platt scaling on out-of-fold predictions (default) or none")
    ap.add_argument("--maturity-days", type=int, default=30, help="drop PRs merged less than this before the cutoff")
    ap.add_argument("--min-positives", type=int, default=50, help="skip a label with fewer bad PRs in the training repos")
    ap.add_argument("--outcome-preds", help="outcome + why predictions to combine for the team evaluation")
    ap.add_argument("--check-labels", action="store_true", help="only count the labels per repo, train nothing")
    ap.add_argument("--no-eval", action="store_true", help="skip the team evaluation")
    ap.add_argument("--latency-prs", type=int, default=1000,
                    help="test PRs to time for latency_ms (0 = all, -1 = none)")
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--cv", action="store_true", help="also run 5-fold cross-validation by repo")
    ap.add_argument("--repeats", type=int, default=1, help="cross-validation runs (reshuffled folds after the first)")
    ap.add_argument("--boot", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--mlflow-experiment", default="riffle-at-open")
    return ap.parse_args(argv)


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    run(parse_args(argv))
    return 0


if __name__ == "__main__":
    sys.exit(main())
