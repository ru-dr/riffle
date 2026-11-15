#!/usr/bin/env python
"""
fold_predictions.py - out-of-fold predictions of the locked outcome and why settings on a by-repo split shared with
another pipeline, so the two can be judged by eval_script.py on the same PRs (see compare_runs.py).

    python pipelines/training/fold_predictions.py
    python pipelines/training/fold_predictions.py --seeds 3 --out eval_results/compare/nishit_by_repo

Split     --folds folds by repo, assigned with the hash Ananth's pipeline uses for its eval/by_repo split
          (ananth_pipeline/pipeline.ipynb: sha1("fold:" + repo lower-cased), first 12 hex digits / 16**12, times
          --folds), so every repo is scored by a model that never saw it, with the same repos held out together.
          --split-from reads a CSV with repo and fold columns instead.
Models    for each fold and seed, the same steps as the test-repo evaluation in tune_lgbm_history.py and
          train_why.py: early stopping on one of the table's own repo folds among the training repos, then a refit on
          all training repos with that many rounds x 1.1.
            outcome  the settings in data/tuning/best_params.json (lgbm_history), at-open features + hist_*
            why      the settings in data/why_tuning/best_params.json, on PRs that went nowhere, + rhist_*
          hist_* and rhist_* are rebuilt by train_experiments.history_features() and train_why.reason_history(),
          from the whole table, point in time (only PRs that closed before a PR opened count).
          The breakage model is not included.
Writes    to --out (default eval_results/compare/nishit_by_repo):
            predictions.csv  one row per PR per seed, in the layout train_why.py writes for eval_script.py
            folds.csv        repo, fold, PRs
            fold_card.json   settings, split, data hash, rounds and time per fold

Needs lightgbm. Rows and features are whatever data/training/train_at_open.csv and features.txt hold.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import train_experiments as te  # noqa: E402  data helpers and history features
import train_why as tw  # noqa: E402  reason labels, reason history, fit_stop_refit, the predictions layout
import tune_lgbm_history as tl  # noqa: E402  the outcome model's default settings

REPO_ROOT = os.path.dirname(os.path.dirname(HERE))


def ananth_fold(repo: str, folds: int) -> int:
    """The fold of a repo in Ananth's by-repo split (his h(repo.lower(), "fold"))."""
    u = int(hashlib.sha1(f"fold:{repo.lower()}".encode()).hexdigest()[:12], 16) / 16 ** 12
    return int(u * folds) % folds


def read_params(path, fallback, what):
    if os.path.isfile(path):
        return json.load(open(path, encoding="utf-8"))["params"], path
    print(f"  no {path}: the {what} model uses the default settings", flush=True)
    return dict(fallback), "defaults"


def _sha256(path, block=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(block), b""):
            h.update(chunk)
    return h.hexdigest()


def run(args):
    os.makedirs(args.out, exist_ok=True)
    tw._lgb()
    df = pd.read_csv(args.data, low_memory=False)
    with open(args.features, encoding="utf-8") as fh:
        feats = [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]
    need = ["repo", "pr_number", "y", "fold", "sample_weight", "created_at", "outcome_3class", "nowhere_reason"]
    miss = [c for c in need + feats if c not in df.columns]
    if miss:
        raise SystemExit("the training CSV is missing: " + ", ".join(miss[:10]) + " (re-run prepare_training_data.py)")

    # ---- the shared split
    if args.split_from:
        sp = pd.read_csv(args.split_from)
        if not {"repo", "fold"} <= set(sp.columns):
            raise SystemExit("--split-from needs repo and fold columns")
        lookup = dict(zip(sp["repo"].astype(str).str.strip().str.lower(), sp["fold"].astype(int)))
        split = df["repo"].astype(str).str.strip().str.lower().map(lookup)
        if split.isna().any():
            gone = sorted(df.loc[split.isna(), "repo"].unique())
            raise SystemExit(f"--split-from has no fold for {len(gone)} repo(s): " + ", ".join(gone[:10]))
        split = split.astype(int).to_numpy()
        split_rule = f"from {os.path.abspath(args.split_from)}"
    else:
        split = np.array([ananth_fold(str(r), args.folds) for r in df["repo"]])
        split_rule = f"Ananth's by-repo hash, {args.folds} folds"
    fold_ids = sorted(np.unique(split))

    # ---- features and labels, exactly as train_why.py builds them
    extra = {"_created_s": te._seconds(df["created_at"]),
             "_closed_s": te._seconds(df["closed_at"]) if "closed_at" in df.columns else np.full(len(df), np.nan)}
    df = pd.concat([df, pd.DataFrame(extra, index=df.index)], axis=1)
    codes, reason, close, n_quick = tw.reason_codes(df, args.labels)
    labels = {"_reason_label": reason.to_numpy(),
              "_close_label": close.to_numpy() if close is not None else np.full(len(df), None, dtype=object)}
    df = pd.concat([df, pd.DataFrame(labels, index=df.index)], axis=1)
    H, _ = te.history_features(df, seed=args.seed)
    RH = tw.reason_history(df, codes, seed=args.seed)
    X_out = np.hstack([df[feats].apply(pd.to_numeric, errors="coerce").to_numpy(float), H.to_numpy(float)])
    X_why = np.hstack([X_out, RH.to_numpy(float)])
    y3, w = df["y"].to_numpy(), df["sample_weight"].to_numpy(float)
    nw = codes >= 0
    merged_rows = (df["outcome_3class"] != "goes_nowhere").to_numpy()
    conditional = {r: (f"label_{r}" in df.columns and df.loc[merged_rows, f"label_{r}"].isna().all())
                   for r in tw.REASONS[:4]}
    out_params, out_src = read_params(args.outcome_params, tl.DEFAULTS, "outcome")
    why_params, why_src = read_params(args.why_params, tw.WHY_PARAMS, "why")
    seeds = [args.seed + s for s in range(args.seeds)]

    line = "=" * 100
    print(f"{line}\nOUT-OF-FOLD PREDICTIONS ON A SHARED BY-REPO SPLIT\n{line}")
    print(f"{len(df):,} PRs from {df['repo'].nunique()} repos; split: {split_rule}.")
    print(f"Outcome: {X_out.shape[1]} features ({len(feats)} + {H.shape[1]} history), settings {out_src}.")
    print(f"Why: {X_why.shape[1]} features (+ {RH.shape[1]} reason history), {args.labels} labels, settings {why_src}"
          + (f"; {n_quick:,} quick 'abandoned' closes counted as unknown" if args.labels == "cleaned" else "") + ".")
    print(f"{len(fold_ids)} folds x {len(seeds)} seeds x 2 models.\n", flush=True)

    P3 = {s: np.full((len(df), 3), np.nan) for s in seeds}
    R5 = {s: np.full((len(df), 5), np.nan) for s in seeds}
    log, t_all = [], time.time()
    for k in fold_ids:
        tst = split == k
        tr = ~tst
        val_fold = df.loc[tr, "fold"].value_counts().idxmax()          # the same rule as train_why.py
        val = tr & (df["fold"] == val_fold).to_numpy()
        fit = tr & ~val
        test_repos = sorted(df.loc[tst, "repo"].unique())
        print(f"fold {k}: {tst.sum():,} test PRs in {len(test_repos)} repos ({', '.join(test_repos)}); "
              f"early stopping on {df.loc[val, 'repo'].nunique()} training repos", flush=True)
        if not (fit & nw).any() or not (val & nw).any():
            raise SystemExit(f"fold {k}: no goes-nowhere PRs with a reason to train or stop the why model on")
        t0 = time.time()
        rec = dict(fold=int(k), test_prs=int(tst.sum()), test_repos=test_repos,
                   early_stopping_repos=sorted(df.loc[val, "repo"].unique()), outcome_rounds=[], why_rounds=[])
        for s in seeds:
            m_out, r_out = tw.fit_stop_refit(out_params, X_out, y3, w, fit, val, s, 3)
            P3[s][tst] = tw.proba(m_out, X_out[tst], 3)
            m_why, r_why = tw.fit_stop_refit(why_params, X_why, codes, w, fit & nw, val & nw, s, 5)
            R5[s][tst] = tw.proba(m_why, X_why[tst], 5)
            rec["outcome_rounds"].append(r_out)
            rec["why_rounds"].append(r_why)
            print(f"  seed {s}: outcome {r_out} rounds, why {r_why} rounds ({time.time() - t0:.0f}s)", flush=True)
        rec["seconds"] = round(time.time() - t0, 1)
        log.append(rec)

    if any(np.isnan(P3[s]).any() for s in seeds):
        raise SystemExit("some PRs were never predicted: a fold is missing from the split")
    pred_path = os.path.join(args.out, "predictions.csv")
    tw.write_predictions(pred_path, df, [P3[s] for s in seeds], [R5[s] for s in seeds], seeds, conditional)
    counts = pd.DataFrame({"repo": df["repo"], "fold": split}).value_counts().rename("prs").reset_index()
    counts.sort_values(["fold", "repo"]).to_csv(os.path.join(args.out, "folds.csv"), index=False)
    man_path = os.path.join(os.path.dirname(os.path.abspath(args.data)), "manifest.json")
    manifest = json.load(open(man_path, encoding="utf-8")) if os.path.isfile(man_path) else {}
    card = dict(
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        script="pipelines/training/fold_predictions.py", split=split_rule,
        data=dict(csv=os.path.abspath(args.data), csv_sha256=_sha256(args.data), prs=int(len(df)),
                  repos=int(df["repo"].nunique()), table_built=manifest.get("built_at"),
                  source_sha256=(manifest.get("source") or {}).get("sha256")),
        features=dict(at_open=len(feats), history=int(H.shape[1]), reason_history=int(RH.shape[1])),
        outcome=dict(params=out_params, params_from=out_src),
        why=dict(params=why_params, params_from=why_src, labels=args.labels, conditional_labels=conditional),
        seeds=seeds, folds=log, seconds=round(time.time() - t_all, 1),
        not_included="the breakage model (p_label_bad, p_label_revert): train_breakage.py has its own split")
    with open(os.path.join(args.out, "fold_card.json"), "w", encoding="utf-8") as fh:
        json.dump(card, fh, indent=2, default=str)
    print(f"\n{line}\nFILES\n{line}")
    print(f"  {pred_path}  ({len(df):,} PRs x {len(seeds)} seeds)")
    print(f"  {os.path.join(args.out, 'folds.csv')}")
    print(f"  {os.path.join(args.out, 'fold_card.json')}")
    print(f"Done in {time.time() - t_all:.0f}s. Next: pipelines/evaluation/compare_runs.py with this predictions.csv.")


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], epilog=__doc__.split("\n\n", 1)[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=os.path.join(REPO_ROOT, "data", "training", "train_at_open.csv"))
    ap.add_argument("--features", default=os.path.join(REPO_ROOT, "data", "training", "features.txt"))
    ap.add_argument("--outcome-params", default=os.path.join(REPO_ROOT, "data", "tuning", "best_params.json"))
    ap.add_argument("--why-params", default=os.path.join(REPO_ROOT, "data", "why_tuning", "best_params.json"))
    ap.add_argument("--out", default=os.path.join(REPO_ROOT, "eval_results", "compare", "nishit_by_repo"))
    ap.add_argument("--folds", type=int, default=5, help="number of folds for the hash split (Ananth's: 5)")
    ap.add_argument("--split-from", help="a CSV with repo and fold columns, instead of the hash split")
    ap.add_argument("--seeds", type=int, default=5, help="seeds per fold (the locked models use 5)")
    ap.add_argument("--seed", type=int, default=0, help="first seed, also used by the history features")
    ap.add_argument("--labels", default="cleaned", choices=["cleaned", "original"],
                    help="reason labels for the why model (the locked model uses cleaned)")
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
