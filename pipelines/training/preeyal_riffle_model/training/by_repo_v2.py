"""
by_repo_v2.py - Preeyal's recipe, re-run without the leaks and on the same by-repo folds as the other two
pipelines, so pipelines/evaluation/compare_runs.py can judge all three on the same PRs.

    python pipelines/training/preeyal_riffle_model/training/by_repo_v2.py
    python pipelines/training/preeyal_riffle_model/training/by_repo_v2.py --seeds 3 --window-from 2026-03-01

What changes from train_final.py (the recipe itself is unchanged)
  Data     the v2 training table (data/training/train_at_open.csv, built by prepare_training_data.py) instead of
           mined_to_input.py's table. v2 has no features read from the PR body as it is today (body_len,
           has_linked_issue, body_headings, body_checklist_items, body_has_test_plan, agent_boilerplate,
           has_description, max_sim_open_prs, max_sim_recent_merged, similar_open_minus_merged), no repo signals
           built from today's author_association (repo_outside_merge_rate, repo_outside_closed_count), and does not
           choose rows by author_association. The clues are the v2 features plus hist_* and rhist_*, rebuilt point
           in time by train_experiments.history_features() and train_why.reason_history(), as before.
  Split    instead of one time split, Ananth's 5 by-repo folds (the hash in pipelines/training/fold_predictions.py):
           each fold's repos are predicted by models that never saw them.
Kept from train_final.py
  - LightGBM: outcome multiclass (31 leaves, 50 per leaf, lambda 1), why multiclass on PRs that went nowhere
    (15 leaves, 80 per leaf, lambda 2), szz binary on merged PRs with a known label; learning rate 0.03,
    subsample and column sample 0.8, early stopping after 150 rounds.
  - every PR weighted 1/sqrt(its repo's PRs), normalised to mean 1 (project_voice).
  - the newest window: training-repo PRs opened on or after --window-from stop the training and set the honesty
    dials (a temperature and per-class shifts, chosen to minimise the per-repo Brier score, bounded).
  - label_bad and label_revert: the fixed rate among merged training-repo PRs (too rare to learn).
  - szz: early stopping on the window when it has at least 200 known answers with 20 yes, else 300 fixed rounds.
  - reasons: train_why.py's cleaned labels (a quick 'abandoned' close counts as unknown), the same as the others.

Writes to --out (default eval_results/compare/preeyal_v2_by_repo):
  predictions.csv          one row per PR per seed, with the dials (the recipe as it ships)
  predictions_nodial.csv   the same models without the dials, to show what the dials do on this split
  folds.csv, fold_card.json

Needs lightgbm and scipy, and the files in pipelines/training it imports.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import warnings
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from scipy.optimize import minimize

HERE = os.path.dirname(os.path.abspath(__file__))
TRAINING = os.path.dirname(os.path.dirname(HERE))           # pipelines/training
REPO_ROOT = os.path.dirname(os.path.dirname(TRAINING))
if TRAINING not in sys.path:
    sys.path.insert(0, TRAINING)
import train_experiments as te  # noqa: E402  data helpers and history features
import train_why as tw  # noqa: E402  reason labels and reason history
from fold_predictions import ananth_fold  # noqa: E402  the shared split

warnings.filterwarnings("ignore", message=".*eval_set.*")
ENDINGS = ("lands_clean", "lands_after_rework", "goes_nowhere")
SZZ_FIXED_FRIENDS = 300


# ===================================================================== the recipe (from train_final.py)
def project_voice(repos):
    repos = pd.Series(np.asarray(repos))
    w = 1.0 / np.sqrt(repos.map(repos.value_counts()).to_numpy(dtype=float))
    return w / w.mean()


def yes_no(series):
    text = pd.Series(series).astype(str).str.strip().str.lower()
    return np.where(text.isin(["true", "1", "1.0"]), 1.0, np.where(text.isin(["false", "0", "0.0"]), 0.0, np.nan))


def score_per_project(chances, answers, repos):
    truth = np.zeros_like(chances)
    truth[np.arange(len(answers)), answers] = 1.0
    return pd.Series(((chances - truth) ** 2).sum(axis=1)).groupby(np.asarray(repos)).mean().mean()


def turn_dial(chances, knobs):
    logs = np.log(np.clip(chances, 1e-6, 1)) * knobs[0] + np.concatenate([[0.0], knobs[1:]])
    logs -= logs.max(axis=1, keepdims=True)
    e = np.exp(logs)
    return e / e.sum(axis=1, keepdims=True)


def set_dial(chances, answers, repos):
    k = chances.shape[1]
    return minimize(lambda x: score_per_project(turn_dial(chances, x), answers, repos),
                    x0=np.concatenate([[1.0], np.zeros(k - 1)]), method="Nelder-Mead",
                    bounds=[(0.3, 3.0)] + [(-3.0, 3.0)] * (k - 1),
                    options={"xatol": 1e-4, "fatol": 1e-6, "maxiter": 4000}).x


def new_model(objective, leaves, min_child, seed, n_estimators=3000):
    lgb = tw._lgb()
    return lgb.LGBMClassifier(
        objective=objective, n_estimators=n_estimators, learning_rate=0.03, num_leaves=leaves,
        min_child_samples=min_child, subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
        reg_lambda=1.0 if leaves == 31 else 2.0, random_state=seed, verbose=-1)


def fit_with_stop(m, X, y, w, Xq, yq, wq):
    lgb = tw._lgb()
    m.fit(X, y, sample_weight=w, eval_set=[(Xq, yq)], eval_sample_weight=[wq],
          callbacks=[lgb.early_stopping(150, verbose=False), lgb.log_evaluation(0)])
    return m


def proba(m, X, k):
    """Probabilities for all k classes, even when a class was missing from the training rows."""
    return tw.proba(m, X, k)


def _sha256(path, block=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(block), b""):
            h.update(chunk)
    return h.hexdigest()


# ===================================================================== output
def write(path, df, P3s, Rs, seeds, conditional, extra):
    """train_why.write_predictions' layout, plus Preeyal's p_label_szz_bug, p_label_bad and p_label_revert."""
    cols = ["repo", "pr_number", "outcome_3class"] + [c for c in te.EXTRA_FOR_EVAL
                                                      if c in df.columns and c != "nowhere_reason"]
    cols += [c for c in tw.LABEL_COLUMNS if c in df.columns]
    cols += [c for c in ("label_bad", "label_bad_observed", "label_revert", "revert_window_closed",
                         "label_szz_bug", "szz_window_closed") if c in df.columns and c not in cols]
    blocks = []
    for i, (P, R, s) in enumerate(zip(P3s, Rs, seeds)):
        b = df[cols].copy()
        b["nowhere_reason"] = df["_reason_label"].to_numpy()
        if "close_reason" in df.columns:
            b["close_reason"] = df["_close_label"].to_numpy()
        b["p_clean"], b["p_rework"], b["p_nowhere"] = P[:, 0], P[:, 1], P[:, 2]
        for j, r in enumerate(tw.REASONS):
            b[f"p_reason_{r}"] = R[:, j]
        for c, j in tw.CLOSE_FROM_REASON.items():
            b[f"p_close_reason_{c}"] = R[:, j]
        merged = P[:, 0] + P[:, 1]
        b["p_label_merged"] = merged
        b["p_label_rework"] = np.where(merged > 0, P[:, 1] / np.where(merged > 0, merged, 1), np.nan)
        for j, r in enumerate(tw.REASONS[:4]):
            b[f"p_label_{r}"] = R[:, j] if conditional.get(r) else P[:, 2] * R[:, j]
        for name, values in extra.items():
            b[name] = values[i]
        b["seed"] = s
        blocks.append(b)
    pd.concat(blocks, ignore_index=True).to_csv(path, index=False)


# ===================================================================== run
def run(args):
    os.makedirs(args.out, exist_ok=True)
    tw._lgb()
    t_all = time.time()
    df = pd.read_csv(args.data, low_memory=False)
    with open(args.features, encoding="utf-8") as fh:
        feats = [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]
    need = ["repo", "pr_number", "created_at", "outcome_3class", "nowhere_reason"]
    miss = [c for c in need + feats if c not in df.columns]
    if miss:
        raise SystemExit("the training CSV is missing: " + ", ".join(miss[:10]) + " (re-run prepare_training_data.py)")
    extra = {"_created_s": te._seconds(df["created_at"]),
             "_closed_s": te._seconds(df["closed_at"]) if "closed_at" in df.columns else np.full(len(df), np.nan)}
    df = pd.concat([df, pd.DataFrame(extra, index=df.index)], axis=1)
    codes, reason, close, n_quick = tw.reason_codes(df, "cleaned")
    labels = {"_reason_label": reason.to_numpy(),
              "_close_label": close.to_numpy() if close is not None else np.full(len(df), None, dtype=object)}
    df = pd.concat([df, pd.DataFrame(labels, index=df.index)], axis=1)
    H, _ = te.history_features(df, seed=args.seed)
    RH = tw.reason_history(df, codes, seed=args.seed)
    clues = feats + list(H.columns) + list(RH.columns)
    X = np.hstack([df[feats].apply(pd.to_numeric, errors="coerce").to_numpy(float),
                   H.to_numpy(float), RH.to_numpy(float)])
    y3 = df["outcome_3class"].map({e: i for i, e in enumerate(ENDINGS)}).to_numpy()
    if np.isnan(y3.astype(float)).any():
        raise SystemExit("some rows have an outcome outside lands_clean / lands_after_rework / goes_nowhere")
    y3 = y3.astype(int)
    repos = df["repo"].astype(str).to_numpy()
    created = pd.to_datetime(df["created_at"], utc=True, errors="coerce")
    merged_rows = (df["outcome_3class"] != "goes_nowhere").to_numpy()
    conditional = {r: (f"label_{r}" in df.columns and df.loc[merged_rows, f"label_{r}"].isna().all())
                   for r in tw.REASONS[:4]}
    nw = codes >= 0
    szz_y = yes_no(df["label_szz_bug"]) if "label_szz_bug" in df.columns else np.full(len(df), np.nan)
    szz_known = merged_rows & ~np.isnan(szz_y)
    rate_cols = [c for c in ("label_bad", "label_revert") if c in df.columns]

    split = np.array([ananth_fold(str(r), args.folds) for r in df["repo"]])
    fold_ids = sorted(np.unique(split))
    seeds = [args.seed + s for s in range(args.seeds)]
    line = "=" * 100
    print(f"{line}\nPREEYAL'S RECIPE, V2 TABLE (NO BODY OR AUTHOR-STATUS FEATURES), ANANTH'S BY-REPO FOLDS\n{line}")
    print(f"{len(df):,} PRs from {df['repo'].nunique()} repos; {len(clues)} clues ({len(feats)} at-open + "
          f"{H.shape[1]} hist_* + {RH.shape[1]} rhist_*); {n_quick:,} quick 'abandoned' closes counted as unknown.")
    print(f"Newest window: training-repo PRs opened on or after {args.window_from} "
          f"(at least {args.min_window:,} PRs). {len(fold_ids)} folds x {len(seeds)} seeds.\n", flush=True)

    shape = lambda k: {s: np.full((len(df), k), np.nan) for s in seeds}  # noqa: E731
    P_dial, P_raw, R_dial, R_raw = shape(3), shape(3), shape(5), shape(5)
    szz_p = {s: np.full(len(df), np.nan) for s in seeds}
    rates = {c: np.full(len(df), np.nan) for c in rate_cols}
    log = []
    for k in fold_ids:
        t0 = time.time()
        tst = split == k
        tr = ~tst
        win = tr & (created >= pd.Timestamp(args.window_from, tz="UTC")).fillna(False).to_numpy()
        window_rule = f"opened on or after {args.window_from}"
        if win.sum() < args.min_window or len(np.unique(y3[win])) < 3:
            order = np.argsort(df["_created_s"].to_numpy()[tr])[::-1]
            win = np.zeros(len(df), bool)
            win[np.flatnonzero(tr)[order[:args.min_window]]] = True
            window_rule = f"the newest {args.min_window:,} training-repo PRs (too few on or after {args.window_from})"
        learn = tr & ~win
        print(f"fold {k}: {tst.sum():,} test PRs in {len(np.unique(repos[tst]))} repos; learn {learn.sum():,}, "
              f"window {win.sum():,} ({window_rule}; goes nowhere {np.mean(y3[win] == 2):.1%})", flush=True)
        rec = dict(fold=int(k), test_prs=int(tst.sum()), test_repos=sorted(np.unique(repos[tst]).tolist()),
                   learn_prs=int(learn.sum()), window_prs=int(win.sum()), window_rule=window_rule,
                   window_goes_nowhere=float(np.mean(y3[win] == 2)), seeds={})
        for c in rate_cols:
            v = yes_no(df.loc[tr & merged_rows, c])
            rates[c][tst] = float(np.nanmean(v)) if np.isfinite(v).any() else np.nan
        wl, wq = project_voice(repos[learn]), project_voice(repos[win])
        lw, ww = learn & nw, win & nw
        ls, ws = learn & szz_known, win & szz_known
        szz_can_stop = ws.sum() >= 200 and np.nansum(szz_y[ws]) >= 20
        for s in seeds:
            end_m = fit_with_stop(new_model("multiclass", 31, 50, s), X[learn], y3[learn], wl,
                                  X[win], y3[win], wq)
            end_knobs = set_dial(proba(end_m, X[win], 3), y3[win], repos[win])
            raw = proba(end_m, X[tst], 3)
            P_raw[s][tst], P_dial[s][tst] = raw, turn_dial(raw, end_knobs)
            why_m = fit_with_stop(new_model("multiclass", 15, 80, s), X[lw], codes[lw], project_voice(repos[lw]),
                                  X[ww], codes[ww], project_voice(repos[ww]))
            why_knobs = set_dial(proba(why_m, X[ww], 5), codes[ww], repos[ww])
            rraw = proba(why_m, X[tst], 5)
            R_raw[s][tst], R_dial[s][tst] = rraw, turn_dial(rraw, why_knobs)
            szz_rounds = None
            if szz_known.any():
                if szz_can_stop:
                    szz_m = fit_with_stop(new_model("binary", 15, 80, s), X[ls], szz_y[ls], project_voice(repos[ls]),
                                          X[ws], szz_y[ws], project_voice(repos[ws]))
                    szz_rounds = int(szz_m.best_iteration_)
                else:
                    both = ls | ws
                    szz_m = new_model("binary", 15, 80, s, n_estimators=SZZ_FIXED_FRIENDS)
                    szz_m.fit(X[both], szz_y[both], sample_weight=project_voice(repos[both]))
                    szz_rounds = SZZ_FIXED_FRIENDS
                szz_p[s][tst] = proba(szz_m, X[tst], 2)[:, 1]
            rec["seeds"][str(s)] = dict(outcome_rounds=int(end_m.best_iteration_), why_rounds=int(why_m.best_iteration_),
                                        szz_rounds=szz_rounds, ending_dial=[float(x) for x in end_knobs],
                                        why_dial=[float(x) for x in why_knobs])
            print(f"  seed {s}: rounds outcome {end_m.best_iteration_}, why {why_m.best_iteration_}, szz {szz_rounds}; "
                  f"dial sureness {end_knobs[0]:.2f}, shifts {end_knobs[1]:+.2f} {end_knobs[2]:+.2f} "
                  f"({time.time() - t0:.0f}s)", flush=True)
        rec["seconds"] = round(time.time() - t0, 1)
        log.append(rec)

    if any(np.isnan(P_dial[s]).any() for s in seeds):
        raise SystemExit("some PRs were never predicted: a fold is missing from the split")
    extra_cols = {}
    if szz_known.any():
        extra_cols["p_label_szz_bug"] = [szz_p[s] for s in seeds]
    for c in rate_cols:
        extra_cols[f"p_{c}"] = [rates[c] for _ in seeds]
    path_dial = os.path.join(args.out, "predictions.csv")
    path_raw = os.path.join(args.out, "predictions_nodial.csv")
    write(path_dial, df, [P_dial[s] for s in seeds], [R_dial[s] for s in seeds], seeds, conditional, extra_cols)
    write(path_raw, df, [P_raw[s] for s in seeds], [R_raw[s] for s in seeds], seeds, conditional, extra_cols)
    counts = pd.DataFrame({"repo": df["repo"], "fold": split}).value_counts().rename("prs").reset_index()
    counts.sort_values(["fold", "repo"]).to_csv(os.path.join(args.out, "folds.csv"), index=False)
    man_path = os.path.join(os.path.dirname(os.path.abspath(args.data)), "manifest.json")
    manifest = json.load(open(man_path, encoding="utf-8")) if os.path.isfile(man_path) else {}
    card = dict(
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        script="pipelines/training/preeyal_riffle_model/training/by_repo_v2.py",
        recipe="Preeyal's train_final.py, unchanged apart from the data and the split",
        split=f"Ananth's by-repo hash, {args.folds} folds",
        data=dict(csv=os.path.abspath(args.data), csv_sha256=_sha256(args.data), prs=int(len(df)),
                  repos=int(df["repo"].nunique()), table_built=manifest.get("built_at"),
                  table_settings=manifest.get("settings")),
        clues=clues, window_from=args.window_from, min_window=args.min_window, seeds=seeds, folds=log,
        seconds=round(time.time() - t_all, 1))
    with open(os.path.join(args.out, "fold_card.json"), "w", encoding="utf-8") as fh:
        json.dump(card, fh, indent=2, default=str)
    print(f"\n{line}\nFILES\n{line}")
    print(f"  {path_dial}  (with the dials: the recipe as it ships)")
    print(f"  {path_raw}  (without the dials)")
    print(f"  {os.path.join(args.out, 'folds.csv')}, {os.path.join(args.out, 'fold_card.json')}")
    print(f"Done in {time.time() - t_all:.0f}s.")


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], epilog=__doc__.split("\n\n", 1)[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=os.path.join(REPO_ROOT, "data", "training", "train_at_open.csv"))
    ap.add_argument("--features", default=os.path.join(REPO_ROOT, "data", "training", "features.txt"))
    ap.add_argument("--out", default=os.path.join(REPO_ROOT, "eval_results", "compare", "preeyal_v2_by_repo"))
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--seeds", type=int, default=3, help="seeds per fold (Preeyal's recipe uses 3)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--window-from", default="2026-03-01",
                    help="the newest window starts here (train_final.py's WINDOW_FROM)")
    ap.add_argument("--min-window", type=int, default=1000,
                    help="if the window has fewer PRs, the newest this many training-repo PRs are used instead")
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
