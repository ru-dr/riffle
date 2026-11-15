#!/usr/bin/env python
"""
tune_why.py - tune the settings of the why model (why a PR will not merge) with 5-fold cross-validation by repo, then
evaluate and build it with the best settings through train_why.py.

    python pipelines/training/tune_why.py
    python pipelines/training/tune_why.py --trials 60 --metric macro_f1

Search    each trial is one set of LightGBM settings (learning rate, leaves, minimum PRs per leaf, depth, row and column
          sampling, L1/L2, minimum split gain), scored by 5-fold cross-validation by repo on every PR that went nowhere
          (early stopping on another repo fold). Score: log loss over the 5 reasons (default; lower is better, keeps
          the probabilities honest) or --metric macro_f1 (higher is better). Trial 1 is the current settings. Optuna's
          search when optuna is installed (only trials in the worst 25% after 3 folds stop early), otherwise random.
Confirm   the best and the current settings are both re-scored on --confirm reshuffled 5-fold splits (log loss and
          macro-F1 for both).
Build     train_why.py runs with the tuned settings: the team's evaluation on the test repos, the baselines, and the
          final why model saved to <out>/model, so the results line up with the earlier why-model run.

Writes to --out (default data/why_tuning at the repo root, which is gitignored): trials.csv, best_params.json,
confirm.csv, and everything train_why.py writes. Needs lightgbm; optuna recommended.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import types
import warnings

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import train_experiments as te  # noqa: E402
import train_why as tw  # noqa: E402
import tune_models as tm  # noqa: E402  the generic suggest()

REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
DEFAULTS = dict(learning_rate=0.03, num_leaves=31, min_child_samples=50, max_depth=-1, subsample=0.8,
                colsample_bytree=0.8, reg_lambda=1.0, reg_alpha=1e-3, min_split_gain=0.0)   # train_why.py's settings
SPACE = {
    "learning_rate": ("log", 0.01, 0.1),
    "num_leaves": ("int_log", 8, 128),
    "min_child_samples": ("int_log", 10, 300),
    "max_depth": ("choice", [-1, 4, 6, 8, 12]),
    "subsample": ("float", 0.5, 1.0),
    "colsample_bytree": ("float", 0.3, 1.0),
    "reg_lambda": ("log", 1e-3, 50.0),
    "reg_alpha": ("log", 1e-3, 10.0),
    "min_split_gain": ("float", 0.0, 0.5),
}


def cv_why(params, X, y, w, folds, seed, report=None):
    """5-fold cross-validation by repo of the why model: scores of the out-of-fold reason probabilities and the best
    boosting rounds of each fold. report(step, partial score) is called after each fold."""
    lgb = tw._lgb()
    ids = sorted(np.unique(folds))
    R = np.full((len(y), 5), np.nan)
    rounds = []
    for i, f in enumerate(ids):
        test = folds == f
        val = folds == ids[(i + 1) % len(ids)]
        fit = ~test & ~val
        m = lgb.LGBMClassifier(objective="multiclass", n_estimators=5000, subsample_freq=1, random_state=seed,
                               verbose=-1, **params)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            m.fit(X[fit], y[fit], sample_weight=w[fit], eval_set=[(X[val], y[val])], eval_sample_weight=[w[val]],
                  callbacks=[lgb.early_stopping(100, verbose=False)])
        R[test] = tw.proba(m, X[test], 5)
        rounds.append(int(m.best_iteration_ or 5000))
        if report is not None:
            done = np.isfinite(R[:, 0])
            report(i, tw.why_scores(R[done], y[done]))
    return tw.why_scores(R, y), rounds


def objective_value(scores, metric):
    """The number to minimise."""
    return scores["log_loss"] if metric == "log_loss" else -scores["macro_f1"]


def search(args, X, y, w, folds):
    try:
        import optuna
    except ImportError:
        optuna = None
    rows, t_start = [], time.time()
    shown = (lambda v: f"log loss {v:.4f}") if args.metric == "log_loss" else (lambda v: f"macro-F1 {-v:.4f}")
    if optuna is not None:
        optuna.logging.set_verbosity(optuna.logging.WARNING)
        study = optuna.create_study(direction="minimize", sampler=optuna.samplers.TPESampler(seed=args.seed),
                                    pruner=optuna.pruners.PercentilePruner(75.0, n_startup_trials=8, n_warmup_steps=2))
        study.enqueue_trial(dict(DEFAULTS))

        def objective(trial):
            params = tm.suggest(SPACE, trial, None)
            t0 = time.time()

            def report(step, sc):
                trial.report(objective_value(sc, args.metric), step)
                if trial.should_prune():
                    trial.set_user_attr("folds_done", step + 1)
                    raise optuna.TrialPruned()

            sc, rounds = cv_why(params, X, y, w, folds, args.seed, report)
            for k, v in sc.items():
                trial.set_user_attr(k, v)
            trial.set_user_attr("rounds", rounds)
            trial.set_user_attr("seconds", round(time.time() - t0, 1))
            return objective_value(sc, args.metric)

        def progress(study, trial):
            el = f"({time.time() - t_start:.0f}s)"
            if trial.state.name == "PRUNED":
                print(f"    trial {trial.number + 1:>3}/{args.trials}: stopped after {trial.user_attrs.get('folds_done', '?')} "
                      f"of 5 folds (worst 25% so far; partial score, not comparable)   best so far "
                      f"{shown(study.best_value)}   {el}", flush=True)
            else:
                a = trial.user_attrs
                print(f"    trial {trial.number + 1:>3}/{args.trials}: log loss {a['log_loss']:.4f}, macro-F1 "
                      f"{a['macro_f1']:.4f}   best so far {shown(study.best_value)}   {el}", flush=True)

        study.optimize(objective, n_trials=args.trials, callbacks=[progress])
        for t in study.trials:
            a = t.user_attrs
            rows.append(dict(trial=t.number, state=t.state.name.lower(), log_loss=a.get("log_loss"),
                             macro_f1=a.get("macro_f1"), accuracy=a.get("accuracy"), **t.params,
                             rounds=a.get("rounds"), seconds=a.get("seconds")))
        method = "optuna (TPE search; trials in the worst 25% after 3 folds stopped early)"
    else:
        rng = np.random.default_rng(args.seed)
        best = np.inf
        for n in range(args.trials):
            params = dict(DEFAULTS) if n == 0 else tm.suggest(SPACE, None, rng)
            t0 = time.time()
            sc, rounds = cv_why(params, X, y, w, folds, args.seed)
            best = min(best, objective_value(sc, args.metric))
            rows.append(dict(trial=n, state="complete", **sc, **params, rounds=rounds, seconds=round(time.time() - t0, 1)))
            print(f"    trial {n + 1:>3}/{args.trials}: log loss {sc['log_loss']:.4f}, macro-F1 {sc['macro_f1']:.4f}   "
                  f"best so far {shown(best)}   ({time.time() - t_start:.0f}s)", flush=True)
        method = "random search (optuna not installed)"
    table = pd.DataFrame(rows)
    done = table[table["state"] == "complete"].copy()
    done["_obj"] = done["log_loss"] if args.metric == "log_loss" else -done["macro_f1"]
    row = done.sort_values("_obj").iloc[0]
    best_params = {}
    for k, (kind, *_a) in SPACE.items():
        v = row[k]
        v = v.item() if hasattr(v, "item") else v
        best_params[k] = int(v) if k in ("num_leaves", "min_child_samples", "max_depth") else float(v)
    return table, best_params, row, method


def run(args):
    os.makedirs(args.out, exist_ok=True)
    df = pd.read_csv(args.data, low_memory=False)
    with open(args.features, encoding="utf-8") as fh:
        feats = [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]
    extra = {"_created_s": te._seconds(df["created_at"]),
             "_closed_s": te._seconds(df["closed_at"]) if "closed_at" in df.columns else np.full(len(df), np.nan)}
    df = pd.concat([df, pd.DataFrame(extra, index=df.index)], axis=1)
    codes, _, _, _ = tw.reason_codes(df, args.labels)
    H, _ = te.history_features(df, seed=args.seed)
    RH = tw.reason_history(df, codes, seed=args.seed)
    X = np.hstack([df[feats].apply(pd.to_numeric, errors="coerce").to_numpy(float), H.to_numpy(float), RH.to_numpy(float)])
    nw = codes >= 0
    if args.tune_on == "train":
        nw &= (df["frames_split"] == "train").to_numpy()
    Xs, ys, ws = X[nw], codes[nw], df["sample_weight"].to_numpy(float)[nw]
    rs = df["repo"].to_numpy()[nw]
    folds = te.repo_folds(df[nw], 5, args.seed) if args.tune_on == "train" else df["fold"].to_numpy()[nw]

    line = "=" * 100
    print(f"{line}\nTUNING THE WHY MODEL\n{line}")
    print(f"{nw.sum():,} PRs that went nowhere ({'all repos' if args.tune_on == 'all' else 'train repos only'}), "
          f"{X.shape[1]} features, {args.labels} labels. Each trial = 5-fold cross-validation by repo; optimising "
          + ("log loss over the 5 reasons (lower is better)." if args.metric == "log_loss" else "macro-F1 (higher is better)."))
    print(f"{args.trials} trials; trial 1 is the current settings.\n")
    table, best, best_row, method = search(args, Xs, ys, ws, folds)
    table.to_csv(os.path.join(args.out, "trials.csv"), index=False)
    with open(os.path.join(args.out, "best_params.json"), "w", encoding="utf-8") as fh:
        json.dump(dict(params=best, log_loss=float(best_row["log_loss"]), macro_f1=float(best_row["macro_f1"]),
                       metric=args.metric, method=method), fh, indent=2)

    print(f"\n  confirming on {args.confirm} reshuffled 5-fold splits ...", flush=True)
    conf = []
    for r in range(args.confirm):
        folds_r = te.repo_folds(pd.DataFrame({"repo": rs}), 5, args.seed + 500 + r)
        for label, params in (("tuned", best), ("current", DEFAULTS)):
            sc, _ = cv_why(params, Xs, ys, ws, folds_r, args.seed + r)
            conf.append(dict(split=r + 1, settings=label, **sc))
            print(f"    split {r + 1}: {label:<8} log loss {sc['log_loss']:.4f}, macro-F1 {sc['macro_f1']:.4f}", flush=True)
    conf = pd.DataFrame(conf)
    conf.to_csv(os.path.join(args.out, "confirm.csv"), index=False)
    piv_ll = conf.pivot(index="split", columns="settings", values="log_loss")
    piv_f1 = conf.pivot(index="split", columns="settings", values="macro_f1")
    gain_ll = piv_ll["current"] - piv_ll["tuned"]
    gain_f1 = piv_f1["tuned"] - piv_f1["current"]

    print(f"\n{line}\nSEARCH RESULT\n{line}")
    done = table[table["state"] == "complete"].sort_values("log_loss" if args.metric == "log_loss" else "macro_f1",
                                                             ascending=args.metric == "log_loss")
    with pd.option_context("display.width", 230, "display.max_columns", 30, "display.float_format", "{:.4f}".format):
        print(done.head(8)[["trial", "log_loss", "macro_f1"] + list(SPACE)].to_string(index=False))
    print("\nBest settings: " + ", ".join(f"{k}={v:.4g}" if isinstance(v, float) else f"{k}={v}" for k, v in best.items()))
    print(f"Confirmed on {len(piv_ll)} reshuffled splits: log loss {piv_ll['tuned'].mean():.4f} tuned vs "
          f"{piv_ll['current'].mean():.4f} current (gain {gain_ll.mean():+.4f}, better on {int((gain_ll > 0).sum())} of "
          f"{len(gain_ll)}); macro-F1 {piv_f1['tuned'].mean():.4f} vs {piv_f1['current'].mean():.4f} "
          f"(gain {gain_f1.mean():+.4f}, better on {int((gain_f1 > 0).sum())} of {len(gain_f1)}).")

    print(f"\n{line}\nEVALUATING AND BUILDING THE TUNED WHY MODEL (train_why.py with the tuned settings)\n{line}", flush=True)
    tw.WHY_PARAMS = {k: v for k, v in best.items()}
    tw_args = types.SimpleNamespace(data=args.data, features=args.features, out=args.out, labels=args.labels,
                                    seeds=args.seeds, cv=False, repeats=1, boot=args.boot, seed=args.seed,
                                    mlflow_experiment=args.mlflow_experiment)
    tw.run(tw_args)
    old = os.path.join(REPO_ROOT, "data", "why_model", "why_vs_baselines.csv")
    new = os.path.join(args.out, "why_vs_baselines.csv")
    if os.path.isfile(old) and os.path.isfile(new):
        a, b = pd.read_csv(old).iloc[0], pd.read_csv(new).iloc[0]
        print(f"\nTest repos, current vs tuned why model: macro-F1 {a['macro_f1']:.4f} -> {b['macro_f1']:.4f}, "
              f"log loss {a['log_loss']:.4f} -> {b['log_loss']:.4f}, accuracy {a['accuracy']:.3f} -> {b['accuracy']:.3f}")
    if args.tune_on == "all":
        print("The test repos also helped choose the settings, so the test numbers are slightly optimistic.")


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], epilog=__doc__.split("\n\n", 1)[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=os.path.join(REPO_ROOT, "data", "training", "train_at_open.csv"))
    ap.add_argument("--features", default=os.path.join(REPO_ROOT, "data", "training", "features.txt"))
    ap.add_argument("--out", default=os.path.join(REPO_ROOT, "data", "why_tuning"))
    ap.add_argument("--labels", default="cleaned", choices=["cleaned", "original"])
    ap.add_argument("--metric", default="log_loss", choices=["log_loss", "macro_f1"], help="what the search optimises")
    ap.add_argument("--trials", type=int, default=40)
    ap.add_argument("--confirm", type=int, default=3)
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--tune-on", default="all", choices=["all", "train"])
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
