#!/usr/bin/env python
"""
train_why.py - the "why" model: for a PR that will not merge, why not (abandoned, duplicate, unwanted, CI or technical,
unknown). LightGBM with the features of lgbm_history plus reason history, judged by the team's evaluation script.

    python pipelines/training/train_why.py
    python pipelines/training/train_why.py --cv --repeats 5

Labels     nowhere_reason of the PRs that went nowhere. By default an "abandoned" PR closed within an hour counts as
           unknown (it is a withdrawal or a silent rejection, not abandonment); --labels original keeps the mined
           labels. close_reason is cleaned the same way (abandoned -> other) so both sections agree.
Features   the locked outcome model's features (at-open + history) plus reason history, from PRs closed before this
           one opened: the repo's mix of reasons over its last 50 PRs that went nowhere, and how many of the author's
           earlier PRs in the repo were abandoned or rejected (duplicate, unwanted, CI or technical).
Model      LightGBM over the 5 reasons, trained only on PRs that went nowhere (sample_weight, early stopping on a
           held-out repo fold, then a refit), --seeds seeds.
Combined   the outcome probabilities come from lgbm_history with the tuned settings (data/tuning/best_params.json),
           trained on the same train repos, so the test repos stay unseen. P(reason) = P(goes nowhere) x
           P(reason | goes nowhere). The predictions file has every column eval_script.py scores: p_clean,
           p_rework, p_nowhere, p_reason_*, p_close_reason_*, p_label_merged, p_label_rework and
           p_label_abandoned/duplicate/unwanted/ci_technical.
Testing    frames_split train/test, judged by eval_script.py (the why-head section and the other labels), with two
           baselines next to the model: the training reason mix and the repo's recent reason mix. --cv adds 5-fold
           cross-validation by repo on the PRs that went nowhere (repeated --repeats times on reshuffled folds).
Final      the why model trained on every PR that went nowhere, saved to <out>/model (boosters, features.txt,
           model_card.json, feature_importance.csv), next to the locked outcome model.

Writes to --out (default data/why_model at the repo root, which is gitignored). Logs to MLflow when installed.
Needs lightgbm, numpy, pandas, scikit-learn and matplotlib (for the evaluation charts).
"""
from __future__ import annotations

import argparse
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
import tune_lgbm_history as tl  # noqa: E402  LightGBM helpers and the outcome model's default settings

REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
REASONS = ("abandoned", "duplicate", "unwanted", "ci_technical", "unknown")
UNKNOWN = REASONS.index("unknown")
CLOSE_REASONS = ("duplicate", "unwanted", "ci_technical", "abandoned", "other")
CLOSE_FROM_REASON = {"duplicate": 1, "unwanted": 2, "ci_technical": 3, "abandoned": 0, "other": 4}   # index in REASONS
LABEL_COLUMNS = ("label_merged", "label_rework", "label_abandoned", "label_duplicate", "label_unwanted",
                 "label_ci_technical", "close_reason")
WHY_PARAMS = dict(learning_rate=0.03, num_leaves=31, min_child_samples=50, subsample=0.8, colsample_bytree=0.8,
                  reg_lambda=1.0)


# ===================================================================== labels and reason history
def reason_codes(df, mode):
    """Reason index (0-4) for PRs that went nowhere with a reason, -1 for every other row; and the cleaned labels."""
    nowhere = (df["outcome_3class"] == "goes_nowhere").to_numpy()
    reason = df["nowhere_reason"].astype(object).where(nowhere, None).copy() if "nowhere_reason" in df.columns \
        else pd.Series([None] * len(df), dtype=object)
    close = df["close_reason"].astype(object).copy() if "close_reason" in df.columns else None
    quick = np.zeros(len(df), bool)
    if mode == "cleaned" and "closed_at" in df.columns:
        hours = (df["_closed_s"] - df["_created_s"]) / 3600.0
        quick = nowhere & (reason == "abandoned").to_numpy() & (hours < 1).fillna(False).to_numpy()
        reason[quick] = "unknown"
        if close is not None:
            close[quick & (close == "abandoned").to_numpy()] = "other"
    codes = reason.map({r: i for i, r in enumerate(REASONS)}).fillna(-1).astype(int).to_numpy()
    return codes, reason, close, int(quick.sum())


def reason_history(df, codes, seed=0, window=50, smooth=3.0):
    """Point-in-time reason features: only PRs that had closed before this PR opened count."""
    created, closed = df["_created_s"].to_numpy(float), df["_closed_s"].to_numpy(float)
    n = len(df)
    names = [f"rhist_repo_{r}_share" for r in REASONS] + ["rhist_author_abandoned", "rhist_author_rejected"]
    F = {k: np.full(n, np.nan) for k in names}
    known = codes >= 0
    for _, idx in df.groupby("repo", sort=True).indices.items():
        sub = idx[known[idx]]
        end = np.where(np.isfinite(closed[sub]), closed[sub], np.inf)
        order = np.argsort(end, kind="stable")
        end_s, c_s = end[order], codes[sub][order]
        cum = np.vstack([np.zeros(5), np.cumsum(np.eye(5)[c_s], axis=0)]) if len(c_s) else np.zeros((1, 5))
        k = np.searchsorted(end_s, created[idx], side="left")
        lo = np.maximum(0, k - window)
        cnt = cum[k] - cum[lo]
        shares = (cnt + smooth / 5) / ((k - lo)[:, None] + smooth)
        for j, r in enumerate(REASONS):
            F[f"rhist_repo_{r}_share"][idx] = shares[:, j]
    if "author_key" in df.columns and df["author_key"].notna().any():
        keyed = df["author_key"].notna().to_numpy()
        pos = np.flatnonzero(keyed)
        for _, gi in df[keyed].groupby(["repo", "author_key"], sort=False).indices.items():
            idx = pos[gi]
            sub = idx[known[idx]]
            ab = np.sort(np.where(np.isfinite(closed[sub]) & (codes[sub] == 0), closed[sub], np.inf))
            rej = np.sort(np.where(np.isfinite(closed[sub]) & np.isin(codes[sub], [1, 2, 3]), closed[sub], np.inf))
            F["rhist_author_abandoned"][idx] = np.searchsorted(ab, created[idx], side="left")
            F["rhist_author_rejected"][idx] = np.searchsorted(rej, created[idx], side="left")
        H = te.fill_unknown_authors(df, pd.DataFrame(F, index=df.index), ["rhist_author_abandoned", "rhist_author_rejected"], seed)
        return H
    return pd.DataFrame(F, index=df.index)


# ===================================================================== models
def _lgb():
    try:
        import lightgbm as lgb
        return lgb
    except ImportError:
        raise SystemExit("train_why.py needs lightgbm: pip install lightgbm")


def proba(model, X, k):
    P = np.zeros((len(X), k))
    raw = model.predict_proba(X)
    for j, c in enumerate(model.classes_):
        P[:, int(c)] = raw[:, j]
    s = P.sum(axis=1, keepdims=True)
    return np.where(s > 0, P / np.where(s > 0, s, 1), 1.0 / k)


def fit_stop_refit(params, X, y, w, fit, val, seed, k, scale=1.1, max_rounds=5000):
    """Early stopping on the val rows, then a refit on fit + val with that many rounds (x scale)."""
    lgb = _lgb()
    base = dict(objective="multiclass", subsample_freq=1, random_state=seed, verbose=-1, **params)
    m = lgb.LGBMClassifier(n_estimators=max_rounds, **base)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        m.fit(X[fit], y[fit], sample_weight=w[fit], eval_set=[(X[val], y[val])], eval_sample_weight=[w[val]],
              callbacks=[lgb.early_stopping(100, verbose=False)])
        rounds = int(m.best_iteration_ or max_rounds)
        final = lgb.LGBMClassifier(n_estimators=max(50, int(rounds * scale)), **base)
        both = fit | val
        final.fit(X[both], y[both], sample_weight=w[both])
    return final, rounds


def macro_f1(y, pred, classes):
    f = []
    for c in classes:
        tp = np.sum((pred == c) & (y == c))
        fp = np.sum((pred == c) & (y != c))
        fn = np.sum((pred != c) & (y == c))
        p = tp / (tp + fp) if tp + fp else 0.0
        r = tp / (tp + fn) if tp + fn else 0.0
        f.append(2 * p * r / (p + r) if p + r else 0.0)
    return float(np.mean(f))


def why_scores(R, y):
    """Macro-F1 over the 4 known reasons (top guess among them, PRs with a known reason), log loss over all 5."""
    known = y != UNKNOWN
    f1 = macro_f1(y[known], R[known, :4].argmax(axis=1), range(4)) if known.any() else np.nan
    ll = float(-np.mean(np.log(np.clip(R[np.arange(len(y)), y], 1e-15, 1))))
    return dict(macro_f1=f1, log_loss=ll, accuracy=float(np.mean(R.argmax(axis=1) == y)))


# ===================================================================== predictions file
def write_predictions(path, df, P3s, Rs, seeds, conditional_labels):
    cols = ["repo", "pr_number", "outcome_3class"] + [c for c in te.EXTRA_FOR_EVAL if c in df.columns and c != "nowhere_reason"]
    cols += [c for c in LABEL_COLUMNS if c in df.columns]
    blocks = []
    for P, R, s in zip(P3s, Rs, seeds):
        b = df[cols].copy()
        b["nowhere_reason"] = df["_reason_label"].to_numpy()
        if "close_reason" in df.columns:
            b["close_reason"] = df["_close_label"].to_numpy()
        b["p_clean"], b["p_rework"], b["p_nowhere"] = P[:, 0], P[:, 1], P[:, 2]
        for j, r in enumerate(REASONS):
            b[f"p_reason_{r}"] = R[:, j]
        for c, j in CLOSE_FROM_REASON.items():
            b[f"p_close_reason_{c}"] = R[:, j]
        merged = P[:, 0] + P[:, 1]
        b["p_label_merged"] = merged
        b["p_label_rework"] = np.where(merged > 0, P[:, 1] / np.where(merged > 0, merged, 1), np.nan)
        for j, r in enumerate(REASONS[:4]):
            b[f"p_label_{r}"] = R[:, j] if conditional_labels.get(r) else P[:, 2] * R[:, j]
        if len(seeds) > 1:
            b["seed"] = s
        blocks.append(b)
    pd.concat(blocks, ignore_index=True).to_csv(path, index=False)


# ===================================================================== run
def run(args):
    os.makedirs(args.out, exist_ok=True)
    df = pd.read_csv(args.data, low_memory=False)
    with open(args.features, encoding="utf-8") as fh:
        feats = [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]
    need = ["repo", "pr_number", "y", "fold", "sample_weight", "created_at", "frames_split", "outcome_3class", "nowhere_reason"]
    miss = [c for c in need + feats if c not in df.columns]
    if miss:
        raise SystemExit("the training CSV is missing: " + ", ".join(miss[:10]))
    extra = {"_created_s": te._seconds(df["created_at"]),
             "_closed_s": te._seconds(df["closed_at"]) if "closed_at" in df.columns else np.full(len(df), np.nan)}
    df = pd.concat([df, pd.DataFrame(extra, index=df.index)], axis=1)
    codes, reason, close, n_quick = reason_codes(df, args.labels)
    labels = {"_reason_label": reason.to_numpy(),
              "_close_label": close.to_numpy() if close is not None else np.full(len(df), None, dtype=object)}
    df = pd.concat([df, pd.DataFrame(labels, index=df.index)], axis=1)
    H, _ = te.history_features(df, seed=args.seed)
    RH = reason_history(df, codes, seed=args.seed)
    out_names = feats + list(H.columns)
    why_names = out_names + list(RH.columns)
    X_out = np.hstack([df[feats].apply(pd.to_numeric, errors="coerce").to_numpy(float), H.to_numpy(float)])
    X_why = np.hstack([X_out, RH.to_numpy(float)])
    y3, w = df["y"].to_numpy(), df["sample_weight"].to_numpy(float)
    nw = codes >= 0
    tr = (df["frames_split"] == "train").to_numpy()
    tst = (df["frames_split"] == "test").to_numpy()
    val_fold = df.loc[tr, "fold"].value_counts().idxmax()
    val = tr & (df["fold"] == val_fold).to_numpy()
    best_path = os.path.join(REPO_ROOT, "data", "tuning", "best_params.json")
    out_params = json.load(open(best_path, encoding="utf-8"))["params"] if os.path.isfile(best_path) else dict(tl.DEFAULTS)
    merged_rows = (df["outcome_3class"] != "goes_nowhere").to_numpy()
    conditional = {r: (f"label_{r}" in df.columns and df.loc[merged_rows, f"label_{r}"].isna().all()) for r in REASONS[:4]}

    line = "=" * 100
    print(f"{line}\nTHE WHY MODEL: why a PR will not merge\n{line}")
    mix = pd.Series(codes[nw]).map(dict(enumerate(REASONS))).value_counts(normalize=True)
    print(f"{nw.sum():,} PRs went nowhere with a reason ({args.labels} labels"
          + (f"; {n_quick:,} 'abandoned' PRs closed within an hour counted as unknown" if args.labels == "cleaned" else "")
          + "). Mix: " + ", ".join(f"{r} {mix.get(r, 0):.0%}" for r in REASONS))
    print(f"Features: {len(why_names)} ({len(out_names)} of the outcome model + {RH.shape[1]} reason history). "
          f"Train repos {df.loc[tr, 'repo'].nunique()}, test repos {df.loc[tst, 'repo'].nunique()}. Outcome model settings: "
          + ("tuned (data/tuning/best_params.json)" if os.path.isfile(best_path) else "defaults"))

    # ---- train on the train repos, predict every row
    seeds = [args.seed + s for s in range(args.seeds)]
    P3s, Rs, rounds, gains = [], [], [], []
    t0 = time.time()
    for s in seeds:
        out_model, _ = fit_stop_refit(out_params, X_out, y3, w, tr & ~val, val, s, 3)
        P3s.append(proba(out_model, X_out, 3))
        why_model, r = fit_stop_refit(WHY_PARAMS, X_why, codes, w, tr & ~val & nw, val & nw, s, 5)
        Rs.append(proba(why_model, X_why, 5))
        rounds.append(r)
        gains.append(why_model.booster_.feature_importance("gain"))
        print(f"  seed {s}: trained ({time.time() - t0:.0f}s)", flush=True)

    # ---- baselines and the model on the test repos' PRs that went nowhere
    test_nw = tst & nw
    yt = codes[test_nw]
    R_model = np.mean(Rs, axis=0)[test_nw]
    train_mix = np.bincount(codes[tr & nw], weights=w[tr & nw], minlength=5)
    train_mix = train_mix / train_mix.sum()
    R_repo = RH[[f"rhist_repo_{r}_share" for r in REASONS]].to_numpy(float)[test_nw]
    R_repo = R_repo / R_repo.sum(axis=1, keepdims=True)
    comp = pd.DataFrame([dict(predictor=name, **why_scores(R, yt)) for name, R in
                         (("why model", R_model), ("baseline: training reason mix", np.tile(train_mix, (len(yt), 1))),
                          ("baseline: repo's recent reason mix", R_repo))])
    comp.to_csv(os.path.join(args.out, "why_vs_baselines.csv"), index=False)

    # ---- the team's evaluation on the test repos
    print("  running the team's evaluation on the test repos ...", flush=True)
    ev = te.load_eval_module()
    pred_path = os.path.join(args.out, "predictions_test.csv")
    write_predictions(pred_path, df[tst], [P[tst] for P in P3s], [R[tst] for R in Rs], seeds, conditional)
    train_csv = os.path.join(args.out, "train_rows_for_eval.csv")
    df.loc[tr, ["repo", "pr_number", "outcome_3class", "created_at"] + (["closed_at"] if "closed_at" in df.columns else [])].to_csv(
        train_csv, index=False)
    feat_file = os.path.join(args.out, "features_used.txt")
    with open(feat_file, "w", encoding="utf-8") as fh:
        fh.write("\n".join(why_names) + "\n")
    summ = te.run_eval(ev, pred_path, "lgbm_history + why", os.path.join(args.out, "eval_test"), train_csv, feat_file, args.boot)
    why_summary = summ.get("why_nowhere")

    # ---- optional cross-validation of the why model by repo
    cv = None
    if args.cv:
        res = []
        for r in range(args.repeats):
            folds = df["fold"].to_numpy() if r == 0 else te.repo_folds(df, df["fold"].nunique(), args.seed + 100 + r)
            ids = sorted(np.unique(folds))
            R_oof = np.full((len(df), 5), np.nan)
            for i, f in enumerate(ids):
                te_f = folds == f
                vf = folds == ids[(i + 1) % len(ids)]
                m, _ = fit_stop_refit(WHY_PARAMS, X_why, codes, w, ~te_f & ~vf & nw, vf & nw, args.seed + r, 5)
                R_oof[te_f & nw] = proba(m, X_why[te_f & nw], 5)
            sc = why_scores(R_oof[nw], codes[nw])
            res.append(sc)
            print(f"  cross-validation {r + 1} of {args.repeats}: macro-F1 {sc['macro_f1']:.3f}, log loss {sc['log_loss']:.3f}",
                  flush=True)
        cv = pd.DataFrame(res)
        cv.to_csv(os.path.join(args.out, "why_cv.csv"), index=False)

    # ---- final why model on every PR that went nowhere
    print(f"  final why model on all {nw.sum():,} PRs that went nowhere ...", flush=True)
    mdir = os.path.join(args.out, "model")
    os.makedirs(mdir, exist_ok=True)
    n_final = max(50, int(np.median(rounds) * 1.2))
    lgb = _lgb()
    fgains = []
    for s in seeds:
        m = lgb.LGBMClassifier(objective="multiclass", n_estimators=n_final, subsample_freq=1, random_state=s, verbose=-1,
                               **WHY_PARAMS)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            m.fit(X_why[nw], codes[nw], sample_weight=w[nw])
        m.booster_.save_model(os.path.join(mdir, f"model_seed{s}.txt"))
        fgains.append(m.booster_.feature_importance("gain"))
    g = np.mean(fgains, axis=0)
    imp = pd.DataFrame({"feature": why_names, "gain_share": g / g.sum()}).sort_values("gain_share", ascending=False)
    imp.to_csv(os.path.join(mdir, "feature_importance.csv"), index=False)
    with open(os.path.join(mdir, "features.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(why_names) + "\n")
    test_row = comp.iloc[0].to_dict()
    card = dict(model="why (LightGBM, outcome-model features + reason history)",
                created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                task="why a PR goes nowhere: P(reason | goes nowhere) in the order " + ", ".join(REASONS),
                how_to_score=("average predict(features) of the model_seed*.txt boosters, features in features.txt order "
                              "(rhist_* from train_why.reason_history). P(reason) = P(goes nowhere) from the outcome model "
                              "(data/tuning/model) x P(reason | goes nowhere)."),
                labels=args.labels, quick_closes_counted_as_unknown=n_quick if args.labels == "cleaned" else 0,
                params=WHY_PARAMS, boosting_rounds=n_final, seeds=seeds, trained_on_prs=int(nw.sum()),
                test=dict(macro_f1=test_row["macro_f1"], log_loss=test_row["log_loss"], accuracy=test_row["accuracy"],
                          eval_why_head=why_summary),
                cv=None if cv is None else dict(macro_f1=float(cv["macro_f1"].mean()), log_loss=float(cv["log_loss"].mean()),
                                                runs=len(cv)))
    with open(os.path.join(mdir, "model_card.json"), "w", encoding="utf-8") as fh:
        json.dump(card, fh, indent=2, default=str)
    log_mlflow(args, card, mdir)
    report(args, comp, summ, cv, imp, conditional, mdir)


def log_mlflow(args, card, mdir):
    try:
        import mlflow
    except ImportError:
        return
    try:
        mlflow.set_experiment(args.mlflow_experiment)
        with mlflow.start_run(run_name="why_model"):
            mlflow.log_params({**WHY_PARAMS, "labels": args.labels, "boosting_rounds": card["boosting_rounds"]})
            mlflow.log_metrics({f"test_{k}": v for k, v in card["test"].items() if isinstance(v, (int, float))})
            mlflow.log_artifacts(mdir, artifact_path="model")
    except Exception as e:                                    # tracking must never stop the run
        print(f"  (MLflow logging failed: {type(e).__name__}: {e})")


def report(args, comp, summ, cv, imp, conditional, mdir):
    line = "=" * 100
    print(f"\n{line}\nRESULTS ON THE TEST REPOS (PRs that went nowhere)\n{line}")
    with pd.option_context("display.width", 200, "display.float_format", "{:.4f}".format):
        print(comp.to_string(index=False))
    print("macro_f1: F1 averaged over abandoned, duplicate, unwanted and CI/technical (PRs with a known reason; 1 = "
          "perfect). log_loss: over all 5 reasons, lower is better. A useful model beats both baselines.")
    s = summ.get("why_nowhere")
    if s:
        print(f"\nTeam evaluation, why-head section: F1 average {s.get('f1_average') or float('nan'):.3f} "
              f"[{s.get('f1_average_lo') or float('nan'):.3f}, {s.get('f1_average_hi') or float('nan'):.3f}], with unknown "
              f"counted as wrong {s.get('f1_average_unknown_counts_as_wrong') or float('nan'):.3f}; the model says unknown "
              f"{s.get('model_says_unknown') or 0:.0%} of the time (really unknown {s.get('really_unknown') or 0:.0%}); "
              f"outcome-and-reason Brier {s.get('outcome_and_reason_brier') or float('nan'):.4f}.")
    per_path = os.path.join(args.out, "eval_test", "why_nowhere_per_reason.csv")
    if os.path.isfile(per_path):
        print("Per reason (precision / recall / F1):")
        for p in pd.read_csv(per_path).to_dict(orient="records"):
            print(f"  {p['reason']:<14} {p['precision']:.2f} / {p['recall']:.2f} / {p['f1']:.2f}   ({int(p['prs']):,} PRs)")
    other = pd.DataFrame(summ.get("other_labels") or [])
    if len(other):
        keep = [c for c in ("label", "prs", "brier", "skill", "auroc", "calibration_error") if c in other.columns]
        print("\nOther labels (team evaluation):")
        with pd.option_context("display.width", 200, "display.float_format", "{:.3f}".format):
            print(other[keep].to_string(index=False))
    if cv is not None:
        print(f"\nCross-validation by repo ({len(cv)} run{'s' if len(cv) > 1 else ''}, all repos): macro-F1 "
              f"{cv['macro_f1'].mean():.3f} +/- {cv['macro_f1'].std(ddof=1) if len(cv) > 1 else 0:.3f}, log loss "
              f"{cv['log_loss'].mean():.3f}")
    if any(conditional.values()):
        print("\nNote: p_label_<reason> is written as P(reason | goes nowhere), because those labels are blank for merged "
              "PRs; the evaluation's consistency check expects P(nowhere) x P(reason), so it may show CHECK there.")
    print("\nTop 10 features of the why model (share of gain):")
    with pd.option_context("display.float_format", "{:.4f}".format):
        print(imp.head(10).to_string(index=False))
    print(f"\n{line}\nFILES\n{line}")
    print(f"  {os.path.join(args.out, 'eval_test', 'report.txt')}  (the team's evaluation: why-head and other-label sections)")
    print(f"  {os.path.join(args.out, 'why_vs_baselines.csv')}" + (", why_cv.csv" if cv is not None else ""))
    print(f"  {mdir}: the final why model (model_seed*.txt, features.txt, model_card.json, feature_importance.csv)")


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], epilog=__doc__.split("\n\n", 1)[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=os.path.join(REPO_ROOT, "data", "training", "train_at_open.csv"))
    ap.add_argument("--features", default=os.path.join(REPO_ROOT, "data", "training", "features.txt"))
    ap.add_argument("--out", default=os.path.join(REPO_ROOT, "data", "why_model"))
    ap.add_argument("--labels", default="cleaned", choices=["cleaned", "original"],
                    help="cleaned: 'abandoned' PRs closed within an hour count as unknown (default)")
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--cv", action="store_true", help="also run 5-fold cross-validation by repo")
    ap.add_argument("--repeats", type=int, default=1, help="cross-validation runs (reshuffled folds after the first)")
    ap.add_argument("--boot", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--mlflow-experiment", default="riffle-at-open")
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
