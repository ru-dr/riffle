"""
by_repo_v3.py - Rudra's per-PR recipe (code/predictions.py), re-run without the leaks and on the same by-repo folds
and rows as the other pipelines, so pipelines/evaluation/compare_runs.py can judge all four on the same PRs.

    python pipelines/training/rudra_riffle_model/code/by_repo_v3.py
    python pipelines/training/rudra_riffle_model/code/by_repo_v3.py --seeds 3

Kept from predictions.py (the recipe)
  - hierarchical LightGBM heads on at-open columns: merge P(merged), rework P(rework | merged), reason
    P(close reason | goes nowhere, 5 classes, missing = other), and P(label | merged) for bad, revert and szz;
    p_clean = pm * (1 - pr), p_rework = pm * pr, p_nowhere = 1 - pm.
  - his LightGBM settings: LG for merge, rework and reason (400 rounds, learning rate 0.05, 31 leaves, 80 per leaf,
    subsample 0.8, column sample 0.5, lambda 10) and LGB for the post-merge labels (400 rounds, 0.03, 15 leaves,
    40 per leaf, lambda 5); no early stopping and no calibration, as in his run.
  - his columns: raw miner columns only, no text or categorical columns (his mk() drops them), and none of his 10
    LLM columns (results/dropsets.json "llm").
What changes, so it matches the others
  - Data: the leak-free training table (data/training/train_at_open.csv from prepare_training_data.py), which drops
    author_association, the body and similarity features, repo_outside_*, ai_agent_authored, agent_name,
    author_prs_merged_repo and the semgrep columns, and gives PRs whose at-open diff is not exact weight 0
    (reweighted per repo and outcome, as Ananth does). The rows are the same outside-contributor PRs as everyone's.
    The table's own engineered and ah_*/rh* history columns are left out: they are not part of Rudra's recipe.
  - label_bad: revert OR hotfix (ADR 0021), observed when both windows closed or either fired; his run used the
    miner's label_bad, which includes CI failures and tracks the date.
  - Split: Ananth's 5 by-repo folds (fold_predictions.ananth_fold); his run was a 75/25 time split.
  - Seeds: --seeds runs (default 3; his run used random_state 0 only), averaged by eval_script.py.

Writes to --out (default eval_results/compare/rudra_v3_by_repo): predictions.csv (one row per PR per seed),
folds.csv, fold_card.json. Needs lightgbm.
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

HERE = os.path.dirname(os.path.abspath(__file__))
TRAINING = os.path.dirname(os.path.dirname(HERE))           # pipelines/training
REPO_ROOT = os.path.dirname(os.path.dirname(TRAINING))
if TRAINING not in sys.path:
    sys.path.insert(0, TRAINING)
import train_experiments as te  # noqa: E402  columns copied for the evaluation
import train_why as tw  # noqa: E402  LightGBM loader and proba() for heads that lack a class
from fold_predictions import ananth_fold  # noqa: E402  the shared split

warnings.filterwarnings("ignore")
LG = dict(n_estimators=400, learning_rate=0.05, num_leaves=31, min_child_samples=80, subsample=0.8, subsample_freq=1,
          colsample_bytree=0.5, reg_lambda=10, verbose=-1)
LGB = dict(n_estimators=400, learning_rate=0.03, num_leaves=15, min_child_samples=40, subsample=0.8, subsample_freq=1,
           colsample_bytree=0.5, reg_lambda=5, verbose=-1)
REASONS = ["abandoned", "duplicate", "unwanted", "ci_technical", "other"]      # close_reason values, as in his run
NOWHERE_NAME = {"other": "unknown"}
RUDRA_LLM = {"llm_touches_authn_authz", "changes_public_api_signature", "exception_swallowed", "security_sensitive_ops",
             "numeric_money_precision", "time_handling_change", "retry_timeout_idempotency_change",
             "unrelated_change_share", "satd_added", "semantic_risk_1to5"}      # results/dropsets.json "llm"
HISTORY_PREFIXES = ("ah_", "rh90_", "rh_open_now", "rh30_", "rh180_")


def flag(s):
    """1 / 0 / NaN from bools, 0/1 or strings."""
    t = pd.Series(s).astype(str).str.strip().str.lower()
    return np.where(t.isin(["true", "1", "1.0"]), 1.0, np.where(t.isin(["false", "0", "0.0"]), 0.0, np.nan))


def fit(params, X, y, w, seed, k=2):
    """One head; a head whose training rows have a single class predicts that class's rate."""
    y = np.asarray(y)
    if len(y) == 0 or len(np.unique(y)) < 2:
        return ("rate", np.bincount(y.astype(int), minlength=k) / max(len(y), 1) if len(y) else np.full(k, 1 / k))
    lgb = tw._lgb()
    extra = dict(objective="multiclass", num_class=k) if k > 2 else {}
    m = lgb.LGBMClassifier(random_state=seed, **extra, **params)
    m.fit(X, y, sample_weight=w)
    return ("model", m)


def predict(head, X, k=2):
    kind, h = head
    if kind == "rate":
        return np.tile(h, (len(X), 1))
    return tw.proba(h, X, k)


def _sha256(path, block=1 << 20):
    hsh = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(block), b""):
            hsh.update(chunk)
    return hsh.hexdigest()


def run(args):
    os.makedirs(args.out, exist_ok=True)
    tw._lgb()
    t_all = time.time()
    df = pd.read_csv(args.data, low_memory=False)
    with open(args.features, encoding="utf-8") as fh:
        table_feats = [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]
    man_path = os.path.join(os.path.dirname(os.path.abspath(args.data)), "manifest.json")
    manifest = json.load(open(man_path, encoding="utf-8")) if os.path.isfile(man_path) else {}
    engineered = {e["feature"] for e in manifest.get("engineered", [])}
    feats = [f for f in table_feats if f not in engineered and not f.startswith(HISTORY_PREFIXES)
             and "__" not in f and f not in RUDRA_LLM]
    left_out = dict(engineered=sorted(set(table_feats) & engineered),
                    history=[f for f in table_feats if f.startswith(HISTORY_PREFIXES)],
                    text_or_categorical=[f for f in table_feats if "__" in f],
                    llm=sorted(set(table_feats) & RUDRA_LLM))
    need = ["repo", "pr_number", "outcome_3class", "sample_weight"]
    miss = [c for c in need + feats if c not in df.columns]
    if miss:
        raise SystemExit("the training CSV is missing: " + ", ".join(miss[:10]) + " (re-run prepare_training_data.py)")
    X = df[feats].apply(pd.to_numeric, errors="coerce").to_numpy(float)
    w_all = df["sample_weight"].to_numpy(float)
    out3 = df["outcome_3class"].astype(str).to_numpy()
    merged = np.isin(out3, ["lands_clean", "lands_after_rework"])
    nowhere = out3 == "goes_nowhere"
    rework_y = (out3 == "lands_after_rework").astype(int)
    cr = df["close_reason"].fillna("other").astype(str) if "close_reason" in df.columns else pd.Series(["other"] * len(df))
    reason_y = cr.map({r: i for i, r in enumerate(REASONS)}).fillna(REASONS.index("other")).astype(int).to_numpy()
    col = lambda c: flag(df[c]) if c in df.columns else np.full(len(df), np.nan)  # noqa: E731
    rv, hf = col("label_revert") == 1, col("label_hotfix") == 1
    seen = ((col("revert_window_closed") == 1) & (col("hotfix_window_closed") == 1)) | rv | hf
    bad_y, bad_obs = (rv | hf).astype(int), merged & seen
    post = {"bad": (bad_obs, bad_y)}
    for k, c in (("revert", "label_revert"), ("szz", "label_szz_bug")):
        v = col(c)
        post[k] = (merged & ~np.isnan(v), np.nan_to_num(v).astype(int))

    split = np.array([ananth_fold(str(r), args.folds) for r in df["repo"]])
    fold_ids = sorted(np.unique(split))
    seeds = [args.seed + s for s in range(args.seeds)]
    line = "=" * 100
    print(f"{line}\nRUDRA'S RECIPE ON THE LEAK-FREE TABLE AND ANANTH'S BY-REPO FOLDS\n{line}")
    print(f"{len(df):,} PRs from {df['repo'].nunique()} repos; {len(feats)} raw miner columns "
          f"({len(table_feats)} in the table; left out: {len(left_out['engineered'])} engineered, "
          f"{len(left_out['history'])} history, {len(left_out['text_or_categorical'])} one-hot, "
          f"{len(left_out['llm'])} of his LLM columns); {int((w_all > 0).sum()):,} PRs carry weight.")
    print(f"label_bad = revert or hotfix: {int(bad_y[bad_obs].sum()):,} bad of {int(bad_obs.sum()):,} observed "
          f"merged PRs. {len(fold_ids)} folds x {len(seeds)} seeds.\n", flush=True)

    P = {s: np.full((len(df), 3), np.nan) for s in seeds}
    R = {s: np.full((len(df), 5), np.nan) for s in seeds}
    B = {s: {k: np.full(len(df), np.nan) for k in post} for s in seeds}
    log = []
    for k in fold_ids:
        t0 = time.time()
        tst = split == k
        tr = ~tst & (w_all > 0)
        print(f"fold {k}: {tst.sum():,} test PRs in {df.loc[tst, 'repo'].nunique()} repos; "
              f"{tr.sum():,} training PRs", flush=True)
        for s in seeds:
            pm = predict(fit(LG, X[tr], merged[tr].astype(int), w_all[tr], s), X[tst])[:, 1]
            m = tr & merged
            pr = predict(fit(LG, X[m], rework_y[m], w_all[m], s), X[tst])[:, 1]
            n = tr & nowhere
            R[s][tst] = predict(fit(LG, X[n], reason_y[n], w_all[n], s, k=5), X[tst], k=5)
            P[s][tst] = np.c_[pm * (1 - pr), pm * pr, 1 - pm]
            for name, (obs, y) in post.items():
                o = tr & obs
                B[s][name][tst] = predict(fit(LGB, X[o], y[o], w_all[o], s), X[tst])[:, 1]
            print(f"  seed {s} done ({time.time() - t0:.0f}s)", flush=True)
        log.append(dict(fold=int(k), test_prs=int(tst.sum()), test_repos=sorted(df.loc[tst, "repo"].unique()),
                        training_prs=int(tr.sum()), seconds=round(time.time() - t0, 1)))

    cols = ["repo", "pr_number", "outcome_3class"] + [c for c in te.EXTRA_FOR_EVAL if c in df.columns]
    cols += [c for c in ("label_merged", "label_rework", "label_abandoned", "label_duplicate", "label_unwanted",
                         "label_ci_technical", "close_reason", "label_revert", "revert_window_closed",
                         "label_hotfix", "hotfix_window_closed", "label_szz_bug", "szz_window_closed")
             if c in df.columns and c not in cols]
    blocks = []
    for s in seeds:
        b = df[cols].copy()
        b["label_bad"] = np.where(merged, bad_y.astype(bool), np.nan)
        b["label_bad_observed"] = np.where(merged, bad_obs, np.nan)
        b["p_clean"], b["p_rework"], b["p_nowhere"] = P[s][:, 0], P[s][:, 1], P[s][:, 2]
        for i, r in enumerate(REASONS):
            b[f"p_reason_{NOWHERE_NAME.get(r, r)}"] = R[s][:, i]
        for r in ("duplicate", "unwanted", "ci_technical", "abandoned", "other"):
            b[f"p_close_reason_{r}"] = R[s][:, REASONS.index(r)]
        pm = P[s][:, 0] + P[s][:, 1]
        b["p_label_merged"] = pm
        b["p_label_rework"] = np.where(pm > 0, P[s][:, 1] / np.where(pm > 0, pm, 1), np.nan)
        for r in ("abandoned", "duplicate", "unwanted", "ci_technical"):
            b[f"p_label_{r}"] = R[s][:, REASONS.index(r)]
        b["p_label_bad"], b["p_label_revert"], b["p_label_szz_bug"] = B[s]["bad"], B[s]["revert"], B[s]["szz"]
        b["seed"] = s
        blocks.append(b)
    pred_path = os.path.join(args.out, "predictions.csv")
    pd.concat(blocks, ignore_index=True).to_csv(pred_path, index=False)
    counts = pd.DataFrame({"repo": df["repo"], "fold": split}).value_counts().rename("prs").reset_index()
    counts.sort_values(["fold", "repo"]).to_csv(os.path.join(args.out, "folds.csv"), index=False)
    card = dict(created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                script="pipelines/training/rudra_riffle_model/code/by_repo_v3.py",
                recipe="Rudra's code/predictions.py heads and settings; leak-free table, Ananth's folds",
                data=dict(csv=os.path.abspath(args.data), csv_sha256=_sha256(args.data), prs=int(len(df)),
                          table_built=manifest.get("built_at")),
                features=feats, left_out=left_out, seeds=seeds, folds=log, seconds=round(time.time() - t_all, 1))
    with open(os.path.join(args.out, "fold_card.json"), "w", encoding="utf-8") as fh:
        json.dump(card, fh, indent=2, default=str)
    print(f"\n{line}\nFILES\n{line}\n  {pred_path}\n  {os.path.join(args.out, 'folds.csv')}, "
          f"{os.path.join(args.out, 'fold_card.json')}\nDone in {time.time() - t_all:.0f}s.")


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], epilog=__doc__.split("\n\n", 1)[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=os.path.join(REPO_ROOT, "data", "training", "train_at_open.csv"))
    ap.add_argument("--features", default=os.path.join(REPO_ROOT, "data", "training", "features.txt"))
    ap.add_argument("--out", default=os.path.join(REPO_ROOT, "eval_results", "compare", "rudra_v3_by_repo"))
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--seed", type=int, default=0)
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
