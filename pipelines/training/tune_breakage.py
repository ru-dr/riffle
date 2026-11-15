#!/usr/bin/env python
"""
tune_breakage.py - tune the breakage model's LightGBM settings with k-fold cross-validation by repo, the way
tune_lgbm_history.py tuned the outcome model, then confirm the result on reshuffled folds.

    python pipelines/training/tune_breakage.py --sources revert hotfix
    python pipelines/training/tune_breakage.py --sources revert hotfix --trials 60 --folds 5 --confirm 5
    python pipelines/training/train_breakage.py --labels label_bad --sources revert hotfix --params tuned --cv

Label      the same as train_breakage.py (its label_setup): --label (default label_bad) built from --sources, on
           merged PRs, with the same weights. PRs merged too recently are dropped there.
Rows       --tune-on train (default): only frames_split's training repos, so the test repos stay unseen for
           train_breakage.py's test; --tune-on all uses every repo (more bad PRs, but the test score is then optimistic).
Search     each trial is one set of settings from tune_lgbm_history.py's search space (learning rate, leaves, minimum
           PRs per leaf, depth, row and column sampling, L1 and L2, minimum split gain). It is scored by --folds-fold
           cross-validation by repo: every repo is predicted once by a model that never saw it, with early stopping on
           the next fold. Score = log loss of those out-of-fold predictions, averaged per repo (lower is better): with
           about 1% bad PRs, log loss separates settings better than Brier. Brier, AUROC, AUPRC and the bad share in
           the riskiest 10% are recorded too. Trial 0 is lgbm_history's tuned settings and trial 1 the conservative
           set, as references. Optuna (TPE, median pruning) when installed, otherwise a seeded random search.
Confirm    the best, lgbm_history's and the conservative settings are re-scored --confirm times on reshuffled repo
           folds; the gain of the best over lgbm_history's is reported per repeat, so it is not a lucky split.

Writes to --out (default data/breakage_tuning, gitignored): trials.csv, confirm.csv and best_params.json, which
train_breakage.py --params tuned reads. Logs to MLflow when installed. Needs lightgbm; optuna is recommended.
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
import train_experiments as te  # noqa: E402  history features, repo folds
import train_breakage as tb  # noqa: E402  the labels, weights and settings, exactly as the model is trained
import tune_lgbm_history as tl  # noqa: E402  the search space

REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
MAX_ROUNDS = 5000
PATIENCE = 100


def repo_logloss(p, y, repos):
    pc = np.clip(p, 1e-15, 1 - 1e-15)
    ll = -(y * np.log(pc) + (1 - y) * np.log(1 - pc))
    return float(pd.Series(ll).groupby(np.asarray(repos)).mean().mean())


def metrics(ev, p, y, repos):
    top = p >= np.quantile(p, 0.9)
    share = float(y.mean())
    return dict(cv_logloss=repo_logloss(p, y, repos), cv_brier=float(np.mean((p - y) ** 2)),
                auroc=ev.auroc_binary(y == 1, p), auprc=ev.average_precision(y == 1, p), share_bad=share,
                lift_top10pct=float(y[top].mean() / share) if top.any() and share > 0 else np.nan)


def cv_score(params, X, yi, w, rows, repos, folds, seed, ev, report=None):
    """k-fold cross-validation by repo over `rows`: train on k-2 folds, stop early on the next one, predict the held-out
    one. Returns the metrics of the out-of-fold predictions and the boosting rounds per fold."""
    lgb = tb._lgb()
    ids = sorted(np.unique(folds[rows]))
    oof = np.full(len(yi), np.nan)
    rounds = []
    for i, f in enumerate(ids):
        hold = rows & (folds == f)
        val = rows & (folds == ids[(i + 1) % len(ids)])
        fit = rows & ~hold & ~val
        if not fit.any() or np.unique(yi[fit]).size < 2:
            continue
        m = lgb.LGBMClassifier(objective="binary", n_estimators=MAX_ROUNDS, subsample_freq=1, random_state=seed,
                               verbose=-1, **params)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            if np.unique(yi[val]).size == 2:
                m.fit(X[fit], yi[fit], sample_weight=w[fit], eval_set=[(X[val], yi[val])],
                      eval_sample_weight=[w[val]], callbacks=[lgb.early_stopping(PATIENCE, verbose=False)])
                rounds.append(int(m.best_iteration_ or MAX_ROUNDS))
            else:
                m.set_params(n_estimators=300)
                m.fit(X[fit], yi[fit], sample_weight=w[fit])
                rounds.append(300)
        oof[hold] = tb.p_bad(m, X[hold])
        if report is not None:
            done = rows & np.isfinite(oof)
            report(i, repo_logloss(oof[done], yi[done].astype(float), repos[done]))
    done = rows & np.isfinite(oof)
    return metrics(ev, oof[done], yi[done].astype(float), repos[done]), rounds


def references():
    """lgbm_history's tuned settings and the conservative set, filled out to the full search space."""
    out = {}
    if os.path.isfile(tb.BEST_PARAMS):
        with open(tb.BEST_PARAMS, encoding="utf-8") as fh:
            out["lgbm_history's settings"] = {**tl.DEFAULTS, **json.load(fh)["params"]}
    out["conservative settings"] = {**tl.DEFAULTS, **tb.CONSERVATIVE_PARAMS}
    return out


def clean(params):
    p = {k: (v.item() if hasattr(v, "item") else v) for k, v in params.items() if k in tl.SPACE}
    for k in ("num_leaves", "min_child_samples", "max_depth"):
        p[k] = int(p[k])
    return p


def search(args, X, yi, w, rows, repos, folds, ev, refs):
    recs = []
    try:
        import optuna
    except ImportError:
        optuna = None
    t_start = time.time()

    def note(n, state, score, best):
        val = f"{score:.5f}" if score is not None else "-"
        print(f"  trial {n + 1:>3}/{args.trials}: {state:<8} log loss {val}   best so far {best:.5f}   "
              f"({time.time() - t_start:.0f}s)", flush=True)

    if optuna is not None:
        optuna.logging.set_verbosity(optuna.logging.WARNING)
        study = optuna.create_study(direction="minimize", sampler=optuna.samplers.TPESampler(seed=args.seed),
                                    pruner=optuna.pruners.MedianPruner(n_startup_trials=5, n_warmup_steps=1))
        for p in refs.values():
            study.enqueue_trial(clean(p))

        def objective(trial):
            params = tl.suggest(trial, None)

            def report(step, value):
                trial.report(value, step)
                if trial.should_prune():
                    raise optuna.TrialPruned()

            m, rounds = cv_score(params, X, yi, w, rows, repos, folds, args.seed, ev, report)
            for k, v in m.items():
                trial.set_user_attr(k, v)
            trial.set_user_attr("rounds", rounds)
            return m["cv_logloss"]

        study.optimize(objective, n_trials=args.trials,
                       callbacks=[lambda s, t: note(t.number, t.state.name.lower(), t.value, s.best_value)])
        for t in study.trials:
            recs.append(dict(trial=t.number, state=t.state.name.lower(), **t.params,
                             **{k: v for k, v in t.user_attrs.items()}))
        method = "optuna (TPE search, median pruning)"
    else:
        rng = np.random.default_rng(args.seed)
        best = np.inf
        ref_list = [clean(p) for p in refs.values()]
        for n in range(args.trials):
            params = ref_list[n] if n < len(ref_list) else tl.suggest(None, rng)
            m, rounds = cv_score(params, X, yi, w, rows, repos, folds, args.seed, ev)
            best = min(best, m["cv_logloss"])
            recs.append(dict(trial=n, state="complete", **params, **m, rounds=rounds))
            note(n, "complete", m["cv_logloss"], best)
        method = "random search (optuna not installed)"
    table = pd.DataFrame(recs)
    done = table[(table["state"] == "complete") & table["cv_logloss"].notna()].sort_values("cv_logloss")
    if done.empty:
        raise SystemExit("no trial finished")
    return table, clean(done.iloc[0].to_dict()), done.iloc[0], method


def confirm(args, X, yi, w, rows, repos, df, ev, candidates):
    """Every candidate on the same --confirm reshuffled repo foldings: the default folds first, then new ones."""
    recs = []
    for r in range(args.confirm):
        folds = (df["fold"].to_numpy() if r == 0 and args.folds == df["fold"].nunique()
                 else te.repo_folds(df, args.folds, args.seed + 1000 + r))
        for name, params in candidates.items():
            m, _ = cv_score(params, X, yi, w, rows, repos, folds, args.seed + r, ev)
            recs.append(dict(repeat=r + 1, settings=name, **m))
        print(f"  confirm {r + 1} of {args.confirm} done", flush=True)
    return pd.DataFrame(recs)


def run(args):
    os.makedirs(args.out, exist_ok=True)
    df, feats = tb.load(args)
    H, _ = te.history_features(df, seed=args.seed)
    names = feats + list(H.columns)
    X = np.hstack([df[feats].apply(pd.to_numeric, errors="coerce").to_numpy(float), H.to_numpy(float)])
    y, mult, ok, _ = tb.label_setup(df, args.label, args)
    yi = np.where(ok, np.nan_to_num(y), 0).astype(int)
    w = df["sample_weight"].to_numpy(float) * mult
    scope = (df["frames_split"] == "train").to_numpy() if args.tune_on == "train" else np.ones(len(df), bool)
    rows = ok & scope
    repos = df["repo"].to_numpy()
    folds = (df["fold"].to_numpy() if args.folds == df["fold"].nunique()
             else te.repo_folds(df, args.folds, args.seed))
    ev = te.load_eval_module()
    line = "=" * 100
    print(f"{line}\nTUNING THE BREAKAGE MODEL ({args.label} = {' OR '.join(args.sources)})\n{line}")
    print(f"{int(rows.sum()):,} merged PRs in {len(np.unique(repos[rows]))} repos ({args.tune_on} repos), "
          f"{int(yi[rows].sum()):,} bad ({yi[rows].mean():.2%}). {args.folds}-fold cross-validation by repo, "
          f"{args.trials} trials, then {args.confirm} confirming repeats. {len(names)} features.")
    if "ci_fail" in args.sources and args.label == "label_bad":
        print("  note: label_bad includes CI, which the miner kept only for recent PRs (ADR 0021): the settings would "
              "be tuned to spot recent PRs. Pass --sources revert hotfix.")
    refs = references()
    table, best, best_row, method = search(args, X, yi, w, rows, repos, folds, ev, refs)
    table.to_csv(os.path.join(args.out, "trials.csv"), index=False)

    candidates = {"best found": best, **{k: clean(v) for k, v in refs.items()}}
    conf = confirm(args, X, yi, w, rows, repos, df, ev, candidates)
    conf.to_csv(os.path.join(args.out, "confirm.csv"), index=False)
    summary = conf.groupby("settings")[["cv_logloss", "cv_brier", "auroc", "auprc", "lift_top10pct"]].agg(["mean", "std"])
    ref_name = "lgbm_history's settings" if "lgbm_history's settings" in candidates else "conservative settings"
    piv = conf.pivot(index="repeat", columns="settings", values="cv_logloss")
    gain = piv[ref_name] - piv["best found"]               # > 0: the best found has the lower log loss
    better = int((gain > 0).sum())

    out = dict(params=best, label=args.label, sources=list(args.sources), unobserved=args.unobserved,
               tune_on=args.tune_on, folds=args.folds, trials=args.trials, method=method,
               cv_logloss=float(best_row["cv_logloss"]), cv_brier=float(best_row["cv_brier"]),
               cv_auroc=float(best_row["auroc"]), cv_auprc=float(best_row["auprc"]),
               confirm=dict(repeats=args.confirm, vs=ref_name, mean_gain_logloss=float(gain.mean()),
                            better_in=f"{better} of {len(gain)} repeats"),
               created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    with open(os.path.join(args.out, "best_params.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, default=str)
    log_mlflow(args, out)

    print(f"\n{line}\nRESULT ({method})\n{line}")
    print("Best settings found: " + ", ".join(f"{k}={v:.4g}" if isinstance(v, float) else f"{k}={v}"
                                              for k, v in best.items()))
    with pd.option_context("display.width", 220, "display.max_columns", 20, "display.float_format", "{:.5f}".format):
        print(f"\nConfirmed on {args.confirm} reshuffled repo foldings (mean and spread):")
        print(summary.to_string())
    print(f"\nThe best found beats {ref_name} in {better} of {len(gain)} repeats; mean log loss gain {gain.mean():+.5f} "
          f"(spread {gain.std(ddof=1) if len(gain) > 1 else float('nan'):.5f}). Lower log loss and Brier are better; "
          "higher AUROC, AUPRC and lift are better.")
    if better <= len(gain) // 2:
        print(f"It is not clearly better than {ref_name}: keep --params outcome, or tune again with more trials.")
    print(f"\nWrote {os.path.join(args.out, 'best_params.json')}. Train with it:\n"
          f"  python pipelines/training/train_breakage.py --labels {args.label} --sources {' '.join(args.sources)} "
          "--params tuned --cv")


def log_mlflow(args, out):
    try:
        import mlflow
    except ImportError:
        return
    try:
        mlflow.set_experiment(args.mlflow_experiment)
        with mlflow.start_run(run_name="breakage_tuning"):
            mlflow.log_params({**out["params"], "label": args.label, "sources": ",".join(args.sources),
                               "tune_on": args.tune_on, "folds": args.folds, "trials": args.trials})
            mlflow.log_metrics({k: out[k] for k in ("cv_logloss", "cv_brier", "cv_auroc", "cv_auprc")
                                if isinstance(out[k], float) and np.isfinite(out[k])})
            mlflow.log_artifacts(args.out, artifact_path="tuning")
    except Exception as e:                                    # tracking must never stop the run
        print(f"  (MLflow logging failed: {type(e).__name__}: {e})")


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], epilog=__doc__.split("\n\n", 1)[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=os.path.join(REPO_ROOT, "data", "training", "train_at_open.csv"))
    ap.add_argument("--features", default=os.path.join(REPO_ROOT, "data", "training", "features.txt"))
    ap.add_argument("--out", default=os.path.join(REPO_ROOT, "data", "breakage_tuning"))
    ap.add_argument("--label", default="label_bad", choices=tb.LABELS)
    ap.add_argument("--sources", nargs="+", default=list(tb.SOURCES), choices=list(tb.SOURCES),
                    help="what makes up label_bad: all three (ADR 0002) or revert hotfix (ADR 0021, proposed)")
    ap.add_argument("--unobserved", default="downweight", choices=["downweight", "negative", "mask"])
    ap.add_argument("--maturity-days", type=int, default=30)
    ap.add_argument("--tune-on", default="train", choices=["train", "all"],
                    help="train: frames_split's training repos only (test repos unseen); all: every repo")
    ap.add_argument("--folds", type=int, default=5, help="k of the k-fold cross-validation by repo")
    ap.add_argument("--trials", type=int, default=40)
    ap.add_argument("--confirm", type=int, default=3, help="repeats on reshuffled repo folds to confirm the gain")
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
