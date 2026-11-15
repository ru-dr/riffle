#!/usr/bin/env python
"""
tune_lgbm_history.py - tune the settings of lgbm_history (LightGBM + previous-rows history features) with 5-fold
cross-validation by repo over the whole training table, then build the final model with the best settings.

    python pipelines/training/tune_lgbm_history.py
    python pipelines/training/tune_lgbm_history.py --trials 60 --out data/tuning

Search    each trial is one set of LightGBM settings (learning rate, leaves, minimum PRs per leaf, depth, row and
          column sampling, L1/L2 regularisation, minimum split gain). It is scored by 5-fold cross-validation by repo
          over all PRs: every repo is predicted once by a model that never saw it, with early stopping on another
          repo fold. Score = Brier of the out-of-fold predictions, averaged per repo (lower is better). Optuna's
          search is used when optuna is installed (clearly bad trials stop after a few folds); otherwise a seeded
          random search.
Confirm   the best settings and the current default settings are both re-scored with cross-validation repeated
          --confirm times on reshuffled repo folds, so the gain is measured fairly.
Evaluate  the best settings are trained on frames_split's train repos and judged by eval_script.py on its test
          repos, exactly like the other models in train_experiments.py.
Final     the optimal model is trained on all PRs with --seeds seeds and saved: LightGBM text files, the feature list
          and a model card with the settings, scores and data version.

Writes to --out (default data/tuning at the repo root, which is gitignored): trials.csv, best_params.json,
confirm.csv, eval_test/ (the evaluation report) and model/ (model_seed<N>.txt, features.txt, model_card.json,
feature_importance.csv). Logs the result to MLflow when mlflow is installed.

With --tune-on all (the default) the test repos also helped choose the settings, so the eval_test score is slightly
optimistic; --tune-on train tunes on frames_split's train repos only and keeps the test repos fully unseen.
Needs lightgbm; optuna is recommended (pip install optuna).
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
import train_experiments as te  # noqa: E402  the experiment script: data loading, history features, evaluation

REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
MAX_ROUNDS = 5000
PATIENCE = 100
# The settings train_experiments.py uses for lgbm_history (the starting point to beat). reg_alpha 0.001 stands in for
# its 0, which is outside the log-scale search range (the two give the same model in practice).
DEFAULTS = dict(learning_rate=0.03, num_leaves=31, min_child_samples=100, max_depth=-1, subsample=0.8,
                colsample_bytree=0.8, reg_lambda=1.0, reg_alpha=1e-3, min_split_gain=0.0)
SPACE = {                                                   # name: (kind, low, high) or (choice, options)
    "learning_rate": ("log", 0.01, 0.1),
    "num_leaves": ("int_log", 8, 128),
    "min_child_samples": ("int_log", 20, 500),
    "max_depth": ("choice", [-1, 4, 6, 8, 12]),
    "subsample": ("float", 0.5, 1.0),
    "colsample_bytree": ("float", 0.3, 1.0),
    "reg_lambda": ("log", 1e-3, 50.0),
    "reg_alpha": ("log", 1e-3, 10.0),
    "min_split_gain": ("float", 0.0, 0.5),
}


def suggest(trial, rng):
    """One set of settings: from Optuna's trial when there is one, otherwise drawn at random from the same ranges."""
    p = {}
    for name, (kind, *a) in SPACE.items():
        if trial is not None:
            if kind == "log":
                p[name] = trial.suggest_float(name, a[0], a[1], log=True)
            elif kind == "int_log":
                p[name] = trial.suggest_int(name, a[0], a[1], log=True)
            elif kind == "float":
                p[name] = trial.suggest_float(name, a[0], a[1])
            else:
                p[name] = trial.suggest_categorical(name, a[0])
        elif kind == "log":
            p[name] = float(np.exp(rng.uniform(np.log(a[0]), np.log(a[1]))))
        elif kind == "int_log":
            p[name] = int(round(np.exp(rng.uniform(np.log(a[0]), np.log(a[1])))))
        elif kind == "float":
            p[name] = float(rng.uniform(a[0], a[1]))
        else:
            p[name] = a[0][int(rng.integers(len(a[0])))]
    return p


def _lgb():
    try:
        import lightgbm as lgb
        return lgb
    except ImportError:
        raise SystemExit("tune_lgbm_history.py needs lightgbm: pip install lightgbm")


def _model(params, seed, n_estimators):
    lgb = _lgb()
    return lgb.LGBMClassifier(objective="multiclass", n_estimators=n_estimators, subsample_freq=1,
                              random_state=seed, verbose=-1, **params)


def repo_brier(P, y, repos):
    codes = pd.Categorical(repos).codes
    R = int(codes.max()) + 1
    return float(te._nanmean(te.group_mean(te.brier_rows(P, y), codes, R)))


def cv_score(params, X, y, w, repos, folds, seed, report=None):
    """5-fold cross-validation by repo with one set of settings. Each fold trains on three folds, stops early on the
    fourth and predicts the fifth. Returns the repo-averaged Brier, the boosting rounds per fold and per-fold scores.
    report(step, score) is called after each fold (Optuna uses it to stop bad trials early)."""
    lgb = _lgb()
    ids = sorted(np.unique(folds))
    P = np.full((len(y), 3), np.nan)
    rounds, fold_scores = [], []
    for i, f in enumerate(ids):
        test = folds == f
        val = folds == ids[(i + 1) % len(ids)]
        fit = ~test & ~val
        m = _model(params, seed, MAX_ROUNDS)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            m.fit(X[fit], y[fit], sample_weight=w[fit], eval_set=[(X[val], y[val])], eval_sample_weight=[w[val]],
                  callbacks=[lgb.early_stopping(PATIENCE, verbose=False)])
        P[test] = te._proba(m, X[test])
        rounds.append(int(m.best_iteration_ or MAX_ROUNDS))
        fold_scores.append(repo_brier(P[test], y[test], repos[test]))
        if report is not None:
            done = np.isfinite(P[:, 0])
            report(i, repo_brier(P[done], y[done], repos[done]))
    return repo_brier(P, y, repos), rounds, fold_scores


def fit_final(params, X, y, w, seed, rounds):
    m = _model(params, seed, rounds)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        m.fit(X, y, sample_weight=w)
    return m


# ===================================================================== search
def search(args, X, y, w, repos, folds):
    """Runs the trials. Returns a table of every trial and the best one."""
    rows = []
    try:
        import optuna
    except ImportError:
        optuna = None
    t_start = time.time()
    if optuna is not None:
        optuna.logging.set_verbosity(optuna.logging.WARNING)
        study = optuna.create_study(direction="minimize", sampler=optuna.samplers.TPESampler(seed=args.seed),
                                    pruner=optuna.pruners.MedianPruner(n_startup_trials=5, n_warmup_steps=1))
        study.enqueue_trial(dict(DEFAULTS))                  # trial 0 = the current settings, as a reference

        def objective(trial):
            params = suggest(trial, None)
            t0 = time.time()

            def report(step, value):
                trial.report(value, step)
                if trial.should_prune():
                    raise optuna.TrialPruned()

            score, rounds, fold_scores = cv_score(params, X, y, w, repos, folds, args.seed, report)
            trial.set_user_attr("rounds", rounds)
            trial.set_user_attr("fold_scores", fold_scores)
            trial.set_user_attr("seconds", round(time.time() - t0, 1))
            return score

        def progress(study, trial):
            state = trial.state.name.lower()
            val = f"{trial.value:.4f}" if trial.value is not None else "-"
            print(f"  trial {trial.number + 1:>3}/{args.trials}: {state:<8} Brier {val}   best so far "
                  f"{study.best_value:.4f}   ({time.time() - t_start:.0f}s)", flush=True)

        study.optimize(objective, n_trials=args.trials, callbacks=[progress])
        for t in study.trials:
            rows.append(dict(trial=t.number, state=t.state.name.lower(), cv_brier=t.value, **t.params,
                             rounds=t.user_attrs.get("rounds"), fold_scores=t.user_attrs.get("fold_scores"),
                             seconds=t.user_attrs.get("seconds")))
        importance = None
        try:
            importance = optuna.importance.get_param_importances(study)
        except Exception:
            pass
        method = "optuna (TPE search, median pruning)"
    else:
        rng = np.random.default_rng(args.seed)
        best = np.inf
        for n in range(args.trials):
            params = dict(DEFAULTS) if n == 0 else suggest(None, rng)
            t0 = time.time()
            score, rounds, fold_scores = cv_score(params, X, y, w, repos, folds, args.seed)
            best = min(best, score)
            rows.append(dict(trial=n, state="complete", cv_brier=score, **params, rounds=rounds,
                             fold_scores=fold_scores, seconds=round(time.time() - t0, 1)))
            print(f"  trial {n + 1:>3}/{args.trials}: complete Brier {score:.4f}   best so far {best:.4f}   "
                  f"({time.time() - t_start:.0f}s)", flush=True)
        importance = None
        method = "random search (optuna not installed)"
    table = pd.DataFrame(rows)
    done = table[table["state"] == "complete"].sort_values("cv_brier")
    if done.empty:
        raise SystemExit("no trial finished")
    best_row = done.iloc[0]
    best_params = {k: (best_row[k].item() if hasattr(best_row[k], "item") else best_row[k]) for k in SPACE}
    for k in ("num_leaves", "min_child_samples", "max_depth"):
        best_params[k] = int(best_params[k])
    return table, best_params, best_row, importance, method


# ===================================================================== run
def run(args):
    os.makedirs(args.out, exist_ok=True)
    _lgb()
    df = pd.read_csv(args.data, low_memory=False)
    with open(args.features, encoding="utf-8") as fh:
        feats = [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]
    miss = [c for c in ["repo", "pr_number", "y", "fold", "sample_weight", "created_at", "frames_split"] + feats
            if c not in df.columns]
    if miss:
        raise SystemExit("the training CSV is missing: " + ", ".join(miss[:10]) + " (re-run prepare_training_data.py)")
    man_path = os.path.join(os.path.dirname(os.path.abspath(args.data)), "manifest.json")
    manifest = json.load(open(man_path, encoding="utf-8")) if os.path.isfile(man_path) else None
    extra = {"_created_s": te._seconds(df["created_at"]),
             "_closed_s": te._seconds(df["closed_at"]) if "closed_at" in df.columns else np.full(len(df), np.nan)}
    df = pd.concat([df, pd.DataFrame(extra, index=df.index)], axis=1)
    H, _ = te.history_features(df, seed=args.seed)
    names = feats + list(H.columns)
    X = np.hstack([df[feats].apply(pd.to_numeric, errors="coerce").to_numpy(float), H.to_numpy(float)])
    y, w, repos = df["y"].to_numpy(), df["sample_weight"].to_numpy(float), df["repo"].to_numpy()

    if args.tune_on == "train":
        use = (df["frames_split"] == "train").to_numpy()
        folds_all = np.full(len(df), -1)
        folds_all[use] = te.repo_folds(df[use], 5, args.seed)
    else:
        use = np.ones(len(df), bool)
        folds_all = df["fold"].to_numpy()
    Xs, ys, ws, rs, fs = X[use], y[use], w[use], repos[use], folds_all[use]

    line = "=" * 100
    print(f"{line}\nTUNING lgbm_history\n{line}")
    print(f"{use.sum():,} PRs from {len(np.unique(rs))} repos ({'all PRs' if args.tune_on == 'all' else 'train repos only'}), "
          f"{len(names)} features ({len(feats)} + {H.shape[1]} history). Each trial = 5-fold cross-validation by repo; "
          f"score = repo-averaged Brier (lower is better).")
    print(f"{args.trials} trials; trial 1 is the current default settings. This takes a while: each trial trains 5 "
          "models.\n")
    table, best, best_row, importance, method = search(args, Xs, ys, ws, rs, fs)
    table.to_csv(os.path.join(args.out, "trials.csv"), index=False)
    default_score = table.loc[table["trial"] == 0, "cv_brier"].iloc[0]
    with open(os.path.join(args.out, "best_params.json"), "w", encoding="utf-8") as fh:
        json.dump(dict(params=best, cv_brier=float(best_row["cv_brier"]), method=method), fh, indent=2)

    # ---- confirm on reshuffled folds: best vs default, same folds and seeds for both
    print(f"\nConfirming: the best and the default settings, each on {args.confirm} reshuffled 5-fold splits ...", flush=True)
    conf = []
    for r in range(args.confirm):
        folds_r = te.repo_folds(pd.DataFrame({"repo": rs}), 5, args.seed + 500 + r)
        for label, params in (("tuned", best), ("default", DEFAULTS)):
            s, rounds, _ = cv_score(params, Xs, ys, ws, rs, folds_r, args.seed + r)
            conf.append(dict(split=r + 1, settings=label, cv_brier=s, rounds=int(np.median(rounds))))
            print(f"  split {r + 1}: {label:<8} Brier {s:.4f}", flush=True)
    conf = pd.DataFrame(conf)
    conf.to_csv(os.path.join(args.out, "confirm.csv"), index=False)
    piv = conf.pivot(index="split", columns="settings", values="cv_brier")
    gain = piv["default"] - piv["tuned"]

    # ---- team evaluation on frames_split's test repos
    print("\nTraining the tuned settings on the train repos and running the team's evaluation on the test repos ...",
          flush=True)
    ev = te.load_eval_module()
    tr = (df["frames_split"] == "train").to_numpy()
    tst = (df["frames_split"] == "test").to_numpy()
    val_fold = df.loc[tr, "fold"].value_counts().idxmax()
    val = tr & (df["fold"] == val_fold).to_numpy()
    fit = tr & ~val
    seeds = [args.seed + s for s in range(args.seeds)]
    Ps = []
    for s in seeds:
        lgb = _lgb()
        m = _model(best, s, MAX_ROUNDS)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            m.fit(X[fit], y[fit], sample_weight=w[fit], eval_set=[(X[val], y[val])], eval_sample_weight=[w[val]],
                  callbacks=[lgb.early_stopping(PATIENCE, verbose=False)])
        final = fit_final(best, X[tr], y[tr], w[tr], s, max(50, int((m.best_iteration_ or MAX_ROUNDS) * 1.1)))
        Ps.append(te._proba(final, X[tst]))
    pred_path = os.path.join(args.out, "predictions_test.csv")
    te.write_predictions(pred_path, df[tst], Ps, seeds)
    train_csv = os.path.join(args.out, "train_rows_for_eval.csv")
    df.loc[tr, [c for c in ("repo", "pr_number", "outcome_3class", "created_at", "closed_at") if c in df.columns]].to_csv(
        train_csv, index=False)
    feat_file = os.path.join(args.out, "features_used.txt")
    with open(feat_file, "w", encoding="utf-8") as fh:
        fh.write("\n".join(names) + "\n")
    test_eval = te.eval_numbers(te.run_eval(ev, pred_path, "lgbm_history_tuned", os.path.join(args.out, "eval_test"),
                                            train_csv, feat_file, args.boot), "test_")

    # ---- the optimal model: all PRs, several seeds
    rounds_final = max(50, int(np.median(best_row["rounds"]) * 1.2))
    print(f"\nTraining the final model on all {len(df):,} PRs with {len(seeds)} seeds ({rounds_final} rounds each) ...",
          flush=True)
    mdir = os.path.join(args.out, "model")
    os.makedirs(mdir, exist_ok=True)
    gains = []
    for s in seeds:
        m = fit_final(best, X, y, w, s, rounds_final)
        m.booster_.save_model(os.path.join(mdir, f"model_seed{s}.txt"))
        gains.append(m.booster_.feature_importance("gain"))
    g = np.mean(gains, axis=0)
    imp = pd.DataFrame({"feature": names, "gain_share": g / g.sum()}).sort_values("gain_share", ascending=False)
    imp.to_csv(os.path.join(mdir, "feature_importance.csv"), index=False)
    with open(os.path.join(mdir, "features.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(names) + "\n")
    card = dict(
        model="lgbm_history (tuned)", created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        task="outcome_3class at PR open: probabilities in the order " + ", ".join(te.CLASSES),
        how_to_score=("average predict(features) of the model_seed*.txt boosters; features in features.txt order. "
                      "The hist_* features are built by train_experiments.history_features() from the repo's earlier "
                      "PRs that had closed before the PR opened."),
        params=best, boosting_rounds=rounds_final, seeds=seeds, trained_on_prs=int(len(df)),
        repos=int(df["repo"].nunique()), tuned_on=args.tune_on, search=method, trials=int(len(table)),
        cv_brier_best_trial=float(best_row["cv_brier"]), cv_brier_default=float(default_score),
        confirm=dict(tuned_mean=float(piv["tuned"].mean()), default_mean=float(piv["default"].mean()),
                     gain_mean=float(gain.mean()), gain_per_split=[float(v) for v in gain]),
        test_eval={k: v for k, v in test_eval.items() if isinstance(v, (int, float)) and np.isfinite(v)},
        data=dict(csv=os.path.abspath(args.data), sha256=(manifest or {}).get("source", {}).get("sha256"),
                  built=(manifest or {}).get("built_at")))
    with open(os.path.join(mdir, "model_card.json"), "w", encoding="utf-8") as fh:
        json.dump(card, fh, indent=2, default=str)
    logged = log_mlflow(args, best, card, mdir)
    report(args, table, best, best_row, default_score, importance, method, piv, gain, test_eval, imp, rounds_final,
           seeds, logged)


def log_mlflow(args, best, card, mdir):
    try:
        import mlflow
    except ImportError:
        return False
    try:
        mlflow.set_experiment(args.mlflow_experiment)
        with mlflow.start_run(run_name="lgbm_history_tuned"):
            mlflow.log_params({**best, "boosting_rounds": card["boosting_rounds"], "tuned_on": args.tune_on,
                               "trials": card["trials"], "seeds": len(card["seeds"])})
            mlflow.log_metrics({"cv_brier_tuned": card["confirm"]["tuned_mean"],
                                "cv_brier_default": card["confirm"]["default_mean"],
                                **{k: v for k, v in card["test_eval"].items()}})
            mlflow.log_artifacts(mdir, artifact_path="model")
        return True
    except Exception as e:                                    # tracking must never stop the run
        print(f"  (MLflow logging failed: {type(e).__name__}: {e})")
        return False


def report(args, table, best, best_row, default_score, importance, method, piv, gain, ev, imp, rounds_final, seeds,
           logged):
    line = "=" * 100
    print(f"\n{line}\nRESULT\n{line}")
    done = table[table["state"] == "complete"].sort_values("cv_brier")
    pruned = int((table["state"] == "pruned").sum())
    print(f"Search: {method}. {len(done)} trials finished" + (f", {pruned} stopped early as clearly worse" if pruned else "")
          + ".")
    cols = ["trial", "cv_brier"] + list(SPACE) + ["seconds"]
    with pd.option_context("display.width", 230, "display.max_columns", 30, "display.float_format", "{:.4f}".format):
        print("\nBest 10 trials (5-fold cross-validation Brier, averaged per repo):")
        print(done.head(10)[cols].to_string(index=False))
    print(f"\nBest settings: " + ", ".join(f"{k}={v:.4g}" if isinstance(v, float) else f"{k}={v}" for k, v in best.items()))
    print(f"Search score: best {best_row['cv_brier']:.4f} vs default {default_score:.4f}")
    if importance:
        print("Which settings mattered most: " + ", ".join(f"{k} {v:.2f}" for k, v in list(importance.items())[:5]))
    print(f"\nConfirmed on {len(piv)} reshuffled 5-fold splits: tuned {piv['tuned'].mean():.4f} vs default "
          f"{piv['default'].mean():.4f}; gain {gain.mean():+.4f} (per split: "
          + ", ".join(f"{v:+.4f}" for v in gain) + ")")
    verdict = ("the tuned settings are better on every split" if (gain > 0).all() else
               "the tuned settings are not better on every split: treat the gain as noise")
    print(f"  -> {verdict}{'; gains under about 0.003 are too small to matter' if abs(gain.mean()) < 0.003 else ''}.")
    print(f"\nTeam evaluation on the test repos ({len(seeds)} seeds averaged): Brier {ev['test_brier']:.4f} "
          f"[{ev['test_brier_lo']:.4f}, {ev['test_brier_hi']:.4f}], skill {ev['test_skill']:+.3f}, calibration error "
          f"{ev['test_calibration_error']:.4f}, AUROC nowhere {ev['test_auroc_nowhere']:.3f}, accuracy "
          f"{ev['test_accuracy']:.3f}, checks {ev['test_checks_passed']} passed / {ev['test_checks_failed']} failed.")
    print("  (default lgbm_history scored Brier 0.497, skill +0.203 there"
          + ("; the test repos also helped choose these settings, so this is slightly optimistic)" if args.tune_on == "all"
             else ")"))
    print("\nTop 10 features of the final model (share of gain):")
    with pd.option_context("display.float_format", "{:.4f}".format):
        print(imp.head(10).to_string(index=False))
    print(f"\n{line}\nFILES\n{line}")
    print(f"  {os.path.join(args.out, 'trials.csv')}, best_params.json, confirm.csv")
    print(f"  {os.path.join(args.out, 'eval_test', 'report.txt')}  (the team's evaluation of the tuned settings)")
    print(f"  {os.path.join(args.out, 'model')}: model_seed*.txt ({len(seeds)} boosters, {rounds_final} rounds), "
          "features.txt, model_card.json, feature_importance.csv" + ("; logged to MLflow" if logged else ""))


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], epilog=__doc__.split("\n\n", 1)[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=os.path.join(REPO_ROOT, "data", "training", "train_at_open.csv"))
    ap.add_argument("--features", default=os.path.join(REPO_ROOT, "data", "training", "features.txt"))
    ap.add_argument("--out", default=os.path.join(REPO_ROOT, "data", "tuning"))
    ap.add_argument("--trials", type=int, default=40, help="settings to try (each one = 5 models)")
    ap.add_argument("--confirm", type=int, default=3, help="reshuffled 5-fold splits used to confirm the winner")
    ap.add_argument("--seeds", type=int, default=5, help="seeds for the evaluation and the final model")
    ap.add_argument("--tune-on", default="all", choices=["all", "train"],
                    help="all = 5-fold over every PR (default); train = only frames_split's train repos")
    ap.add_argument("--boot", type=int, default=2000, help="resamples of repos for the evaluation's 95%% ranges")
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
