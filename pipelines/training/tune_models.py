#!/usr/bin/env python
"""
tune_models.py - the same deep tuning as tune_lgbm_history.py, for the other top models: lgbm, logreg, mlp_big, mlp.

    python pipelines/training/tune_models.py
    python pipelines/training/tune_models.py --models lgbm logreg --trials 30

For each model:
  Search    each trial is one set of settings, scored by 5-fold cross-validation by repo over all PRs (every repo is
            predicted once by a model that never saw it; early stopping on another repo fold). Score = Brier of the
            out-of-fold predictions, averaged per repo (lower is better). Trial 1 is the model's current settings.
            Optuna's search when optuna is installed (only trials in the worst 25% after 3 folds are stopped early),
            otherwise a seeded random search.
  Confirm   the best and the current settings are both re-scored on --confirm reshuffled 5-fold splits.
  Evaluate  the tuned model is trained on frames_split's train repos and judged by eval_script.py on its test repos.
  Final     the tuned model is trained on all PRs (--seeds seeds for models with randomness) and saved with a model card.

  What is tuned
    lgbm      learning rate, leaves, minimum PRs per leaf, depth, row/column sampling, L1/L2, minimum split gain
    logreg    regularisation strength C, and how long-tailed a feature must be to get a log transform
    mlp       weight decay, learning rate, batch size, width (x0.5, x1, x1.5 of 512-256-128)
    mlp_big   the same, on 1024-512-256-128

Writes to --out (default data/tuning_models at the repo root, which is gitignored): <model>/trials.csv,
best_params.json, confirm.csv, eval_test/ and model/ (lgbm: model_seed<N>.txt; logreg and the nets: model_seed<N>.pkl
holding the model and its preprocessing; features.txt, model_card.json), plus summary.csv comparing every tuned model
(the tuned lgbm_history from data/tuning is included when it exists). Logs to MLflow when mlflow is installed.

With --tune-on all (the default) the test repos also helped choose the settings, so eval_test is slightly
optimistic; --tune-on train keeps them fully unseen. Needs scikit-learn (and lightgbm for lgbm); optuna recommended.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import pickle
import sys
import time
import warnings
from datetime import datetime, timezone

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import train_experiments as te  # noqa: E402  data loading, history features, evaluation helpers
import tune_lgbm_history as tl  # noqa: E402  LightGBM search space and helpers

REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
TRIALS = {"lgbm": 40, "logreg": 15, "mlp": 25, "mlp_big": 25}


# ===================================================================== preprocessing for logreg and the nets
def prep_fit(X, log_skew=3.0):
    """Median fill, log for long-tailed counts (skew above log_skew), standard scaling: learned on training rows only."""
    med = np.nanmedian(X, axis=0)
    med = np.where(np.isfinite(med), med, 0.0)
    Z = np.where(np.isnan(X), med, X)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        skew = pd.DataFrame(Z).skew().to_numpy()
    logcols = (Z.min(axis=0) >= 0) & (np.nan_to_num(skew) > log_skew)
    Z[:, logcols] = np.log1p(Z[:, logcols])
    mu, sd = Z.mean(axis=0), Z.std(axis=0)
    return dict(med=med, logcols=logcols, mu=mu, sd=np.where(sd > 0, sd, 1.0))


def prep_apply(X, st):
    Z = np.where(np.isnan(X), st["med"], X)
    Z[:, st["logcols"]] = np.log1p(np.clip(Z[:, st["logcols"]], 0, None))
    return np.clip((Z - st["mu"]) / st["sd"], -10, 10)


class Fitted:
    """A trained model with its preprocessing: predict(X) gives probabilities in te.CLASSES order."""

    def __init__(self, model, prep=None, kind="sklearn"):
        self.model, self.prep, self.kind = model, prep, kind

    def predict(self, X):
        return te._proba(self.model, prep_apply(X, self.prep) if self.prep is not None else X)

    def save(self, path_without_ext):
        if self.kind == "lgbm":
            self.model.booster_.save_model(path_without_ext + ".txt")
        else:
            with open(path_without_ext + ".pkl", "wb") as fh:
                pickle.dump(dict(model=self.model, prep=self.prep), fh)


# ===================================================================== what each model tunes and how it trains
class Tuner:
    name = ""
    history = False
    random = True
    space = {}
    defaults = {}

    def fold(self, params, X, y, w, fit, val, test, seed):
        """Train on the fit rows (val rows for early stopping); return probabilities for the test rows and the
        number of rounds/epochs that worked best (0 when not applicable)."""
        raise NotImplementedError

    def final(self, params, X, y, w, seed, steps):
        """Train on all given rows with the steps learned in cross-validation; returns a Fitted."""
        raise NotImplementedError


class LGBMTuner(Tuner):
    name = "lgbm"
    space = tl.SPACE
    defaults = tl.DEFAULTS

    def fold(self, params, X, y, w, fit, val, test, seed):
        lgb = tl._lgb()
        m = tl._model(params, seed, tl.MAX_ROUNDS)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            m.fit(X[fit], y[fit], sample_weight=w[fit], eval_set=[(X[val], y[val])], eval_sample_weight=[w[val]],
                  callbacks=[lgb.early_stopping(tl.PATIENCE, verbose=False)])
        return te._proba(m, X[test]), int(m.best_iteration_ or tl.MAX_ROUNDS)

    def final(self, params, X, y, w, seed, steps):
        return Fitted(tl.fit_final(params, X, y, w, seed, max(50, int(steps * 1.2))), kind="lgbm")


class LogRegTuner(Tuner):
    name = "logreg"
    random = False
    space = {"C": ("log", 1e-4, 100.0), "log_skew": ("choice", [2.0, 3.0, 5.0, 1e9])}   # 1e9 = never log
    defaults = {"C": 0.5, "log_skew": 3.0}

    def _fit(self, params, X, y, w):
        from sklearn.linear_model import LogisticRegression
        st = prep_fit(X, params["log_skew"])
        m = LogisticRegression(C=params["C"], max_iter=3000)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            m.fit(prep_apply(X, st), y, sample_weight=w)
        return Fitted(m, st)

    def fold(self, params, X, y, w, fit, val, test, seed):
        train = fit | val                                  # no early stopping: train on every non-test row
        return self._fit(params, X[train], y[train], w[train]).predict(X[test]), 0

    def final(self, params, X, y, w, seed, steps):
        return self._fit(params, X, y, w)


class MLPTuner(Tuner):
    name = "mlp"
    base = (512, 256, 128)
    space = {"alpha": ("log", 1e-6, 1e-1), "learning_rate_init": ("log", 1e-4, 3e-3),
             "batch_size": ("choice", [128, 256, 512]), "width": ("choice", [0.5, 1.0, 1.5])}
    defaults = {"alpha": 3e-4, "learning_rate_init": 1e-3, "batch_size": 256, "width": 1.0}
    max_epochs, patience = 60, 5

    def layers(self, p):
        return tuple(max(8, int(round(n * p["width"]))) for n in self.base)

    def _net(self, params, seed):
        from sklearn.neural_network import MLPClassifier
        return MLPClassifier(hidden_layer_sizes=self.layers(params), alpha=params["alpha"],
                             batch_size=int(params["batch_size"]), learning_rate_init=params["learning_rate_init"],
                             random_state=seed)

    @staticmethod
    def _epoch(m, Z, y, w):
        try:
            m.partial_fit(Z, y, sample_weight=w, classes=np.arange(3))
        except TypeError:                                  # scikit-learn before 1.7: no weights for MLP
            m.partial_fit(Z, y, classes=np.arange(3))

    def fold(self, params, X, y, w, fit, val, test, seed):
        st = prep_fit(X[fit])
        Zf, Zv, Zt = prep_apply(X[fit], st), prep_apply(X[val], st), prep_apply(X[test], st)
        m = self._net(params, seed)
        best, best_w, best_epoch, bad = np.inf, None, 1, 0
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            for epoch in range(1, self.max_epochs + 1):
                self._epoch(m, Zf, y[fit], w[fit])
                P = te._proba(m, Zv)
                loss = float(np.average(-np.log(np.clip(P[np.arange(val.sum()), y[val]], 1e-15, 1)), weights=w[val]))
                if loss < best - 1e-4:
                    best, best_w, best_epoch, bad = loss, copy.deepcopy((m.coefs_, m.intercepts_)), epoch, 0
                else:
                    bad += 1
                    if bad >= self.patience:
                        break
        m.coefs_, m.intercepts_ = best_w
        return te._proba(m, Zt), best_epoch

    def final(self, params, X, y, w, seed, steps):
        st = prep_fit(X)
        Z = prep_apply(X, st)
        m = self._net(params, seed)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            for _ in range(max(1, int(round(steps)))):
                self._epoch(m, Z, y, w)
        return Fitted(m, st)


class MLPBigTuner(MLPTuner):
    name = "mlp_big"
    base = (1024, 512, 256, 128)
    defaults = {"alpha": 1e-3, "learning_rate_init": 1e-3, "batch_size": 256, "width": 1.0}


TUNERS = {"lgbm": LGBMTuner, "logreg": LogRegTuner, "mlp": MLPTuner, "mlp_big": MLPBigTuner}


# ===================================================================== search
def suggest(space, trial, rng):
    p = {}
    for name, (kind, *a) in space.items():
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


def cv_run(tuner, params, X, y, w, repos, folds, seed, report=None):
    """5-fold cross-validation by repo: repo-averaged Brier and the best rounds/epochs of each fold."""
    ids = sorted(np.unique(folds))
    P = np.full((len(y), 3), np.nan)
    steps = []
    for i, f in enumerate(ids):
        test = folds == f
        val = folds == ids[(i + 1) % len(ids)]
        fit = ~test & ~val
        P[test], s = tuner.fold(params, X, y, w, fit, val, test, seed)
        steps.append(s)
        if report is not None:
            done = np.isfinite(P[:, 0])
            report(i, tl.repo_brier(P[done], y[done], repos[done]))
    return tl.repo_brier(P, y, repos), steps


def search(tuner, n_trials, args, X, y, w, repos, folds):
    try:
        import optuna
    except ImportError:
        optuna = None
    rows = []
    t_start = time.time()
    if optuna is not None:
        optuna.logging.set_verbosity(optuna.logging.WARNING)
        study = optuna.create_study(direction="minimize", sampler=optuna.samplers.TPESampler(seed=args.seed),
                                    pruner=optuna.pruners.PercentilePruner(75.0, n_startup_trials=8, n_warmup_steps=2))
        study.enqueue_trial(dict(tuner.defaults))

        def objective(trial):
            params = suggest(tuner.space, trial, None)
            t0 = time.time()

            def report(step, value):
                trial.report(value, step)
                if trial.should_prune():
                    trial.set_user_attr("folds_done", step + 1)
                    raise optuna.TrialPruned()

            score, steps = cv_run(tuner, params, X, y, w, repos, folds, args.seed, report)
            trial.set_user_attr("steps", steps)
            trial.set_user_attr("seconds", round(time.time() - t0, 1))
            return score

        def progress(study, trial):
            elapsed = f"({time.time() - t_start:.0f}s)"
            if trial.state.name == "PRUNED":
                k = trial.user_attrs.get("folds_done", "?")
                print(f"    trial {trial.number + 1:>3}/{n_trials}: stopped after {k} of 5 folds (worst 25% so far; "
                      f"partial score, not comparable)   best so far {study.best_value:.4f}   {elapsed}", flush=True)
            else:
                print(f"    trial {trial.number + 1:>3}/{n_trials}: Brier {trial.value:.4f}   best so far "
                      f"{study.best_value:.4f}   {elapsed}", flush=True)

        study.optimize(objective, n_trials=n_trials, callbacks=[progress])
        for t in study.trials:
            rows.append(dict(trial=t.number, state=t.state.name.lower(),
                             cv_brier=t.value if t.state.name == "COMPLETE" else None, **t.params,
                             steps=t.user_attrs.get("steps"), seconds=t.user_attrs.get("seconds")))
        method = "optuna (TPE search; trials in the worst 25% after 3 folds stopped early)"
    else:
        rng = np.random.default_rng(args.seed)
        best = np.inf
        for n in range(n_trials):
            params = dict(tuner.defaults) if n == 0 else suggest(tuner.space, None, rng)
            t0 = time.time()
            score, steps = cv_run(tuner, params, X, y, w, repos, folds, args.seed)
            best = min(best, score)
            rows.append(dict(trial=n, state="complete", cv_brier=score, **params, steps=steps,
                             seconds=round(time.time() - t0, 1)))
            print(f"    trial {n + 1:>3}/{n_trials}: Brier {score:.4f}   best so far {best:.4f}   "
                  f"({time.time() - t_start:.0f}s)", flush=True)
        method = "random search (optuna not installed)"
    table = pd.DataFrame(rows)
    done = table[table["state"] == "complete"].sort_values("cv_brier")
    if done.empty:
        raise SystemExit(f"{tuner.name}: no trial finished")
    row = done.iloc[0]
    best_params = {}
    for k, (kind, *_a) in tuner.space.items():
        v = row[k]
        v = v.item() if hasattr(v, "item") else v
        best_params[k] = int(v) if kind == "int_log" or (kind == "choice" and float(v).is_integer() and k != "width"
                                                         and k != "log_skew") else v
    return table, best_params, row, method


# ===================================================================== one model, end to end
def tune_one(name, args, data, ev, train_csv):
    tuner = TUNERS[name]()
    df, X_base, H, feats = data["df"], data["X"], data["H"], data["feats"]
    X = np.hstack([X_base, H.to_numpy(float)]) if tuner.history else X_base
    names = feats + (list(H.columns) if tuner.history else [])
    y, w, repos = df["y"].to_numpy(), df["sample_weight"].to_numpy(float), df["repo"].to_numpy()
    use, folds_use = data["use"], data["folds_use"]
    Xs, ys, ws, rs = X[use], y[use], w[use], repos[use]
    mdir = os.path.join(args.out, name)
    os.makedirs(mdir, exist_ok=True)
    n_trials = args.trials or TRIALS[name]
    line = "-" * 100
    print(f"\n{line}\n{name}: {n_trials} trials, each 5-fold cross-validation by repo on {use.sum():,} PRs\n{line}", flush=True)
    t0 = time.time()
    table, best, best_row, method = search(tuner, n_trials, args, Xs, ys, ws, rs, folds_use)
    table.to_csv(os.path.join(mdir, "trials.csv"), index=False)
    default_score = table.loc[table["trial"] == 0, "cv_brier"].iloc[0]
    with open(os.path.join(mdir, "best_params.json"), "w", encoding="utf-8") as fh:
        json.dump(dict(params=best, cv_brier=float(best_row["cv_brier"]), method=method), fh, indent=2, default=str)

    print(f"  confirming on {args.confirm} reshuffled 5-fold splits ...", flush=True)
    conf = []
    for r in range(args.confirm):
        folds_r = te.repo_folds(pd.DataFrame({"repo": rs}), 5, args.seed + 500 + r)
        for label, params in (("tuned", best), ("default", tuner.defaults)):
            s, _ = cv_run(tuner, params, Xs, ys, ws, rs, folds_r, args.seed + r)
            conf.append(dict(split=r + 1, settings=label, cv_brier=s))
    conf = pd.DataFrame(conf)
    conf.to_csv(os.path.join(mdir, "confirm.csv"), index=False)
    piv = conf.pivot(index="split", columns="settings", values="cv_brier")
    gain = piv["default"] - piv["tuned"]

    print("  team evaluation on the test repos ...", flush=True)
    tr = (df["frames_split"] == "train").to_numpy()
    tst = (df["frames_split"] == "test").to_numpy()
    val_fold = df.loc[tr, "fold"].value_counts().idxmax()
    val = tr & (df["fold"] == val_fold).to_numpy()
    seeds = [args.seed + s for s in range(args.seeds)] if tuner.random else [args.seed]
    Ps = []
    for s in seeds:
        _, steps = tuner.fold(best, X, y, w, tr & ~val, val, tst, s)
        Ps.append(tuner.final(best, X[tr], y[tr], w[tr], s, steps).predict(X[tst]))
    pred_path = os.path.join(mdir, "predictions_test.csv")
    te.write_predictions(pred_path, df[tst], Ps, seeds)
    feat_file = os.path.join(mdir, "features_used.txt")
    with open(feat_file, "w", encoding="utf-8") as fh:
        fh.write("\n".join(names) + "\n")
    test_eval = te.eval_numbers(te.run_eval(ev, pred_path, f"{name}_tuned", os.path.join(mdir, "eval_test"),
                                            train_csv, feat_file, args.boot), "test_")

    steps_final = float(np.median(best_row["steps"])) if best_row["steps"] is not None else 0.0
    print(f"  final model on all {len(df):,} PRs ({len(seeds)} seed{'s' if len(seeds) > 1 else ''}) ...", flush=True)
    model_dir = os.path.join(mdir, "model")
    os.makedirs(model_dir, exist_ok=True)
    for s in seeds:
        tuner.final(best, X, y, w, s, steps_final).save(os.path.join(model_dir, f"model_seed{s}"))
    with open(os.path.join(model_dir, "features.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(names) + "\n")
    card = dict(model=f"{name} (tuned)", created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                task="outcome_3class at PR open: probabilities in the order " + ", ".join(te.CLASSES),
                how_to_score=("lgbm: average predict() of the model_seed*.txt LightGBM boosters. logreg and nets: "
                              "model_seed*.pkl holds {'model', 'prep'}; apply tune_models.prep_apply(X, prep) then "
                              "predict_proba. Features in features.txt order."),
                params=best, steps=steps_final, seeds=seeds, trained_on_prs=int(len(df)), repos=int(df["repo"].nunique()),
                tuned_on=args.tune_on, search=method, trials=int(len(table)),
                cv_brier_best_trial=float(best_row["cv_brier"]), cv_brier_default=float(default_score),
                confirm=dict(tuned_mean=float(piv["tuned"].mean()), default_mean=float(piv["default"].mean()),
                             gain_mean=float(gain.mean()), gain_per_split=[float(v) for v in gain]),
                test_eval={k: v for k, v in test_eval.items() if isinstance(v, (int, float)) and np.isfinite(v)},
                data=dict(csv=os.path.abspath(args.data), sha256=data["sha"]))
    with open(os.path.join(model_dir, "model_card.json"), "w", encoding="utf-8") as fh:
        json.dump(card, fh, indent=2, default=str)
    log_mlflow(args, name, best, card, model_dir)
    mins = (time.time() - t0) / 60
    print(f"  {name}: best settings " + ", ".join(f"{k}={v:.4g}" if isinstance(v, float) else f"{k}={v}"
                                                   for k, v in best.items()))
    print(f"  {name}: confirmed {piv['tuned'].mean():.4f} tuned vs {piv['default'].mean():.4f} default "
          f"(gain {gain.mean():+.4f}; better on {int((gain > 0).sum())} of {len(gain)} splits). Test Brier "
          f"{test_eval['test_brier']:.4f}, skill {test_eval['test_skill']:+.3f}. ({mins:.0f} min)", flush=True)
    return dict(model=name, cv_default=float(piv["default"].mean()), cv_tuned=float(piv["tuned"].mean()),
                cv_gain=float(gain.mean()), better_on_all_splits=bool((gain > 0).all()),
                **{k: test_eval.get(f"test_{k}") for k in ("brier", "brier_lo", "brier_hi", "skill", "calibration_error",
                                                           "auroc_nowhere", "accuracy", "checks_passed", "checks_failed")},
                minutes=round(mins, 1))


def log_mlflow(args, name, best, card, model_dir):
    try:
        import mlflow
    except ImportError:
        return
    try:
        mlflow.set_experiment(args.mlflow_experiment)
        with mlflow.start_run(run_name=f"{name}_tuned"):
            mlflow.log_params({**{k: v for k, v in best.items()}, "tuned_on": args.tune_on, "trials": card["trials"]})
            mlflow.log_metrics({"cv_brier_tuned": card["confirm"]["tuned_mean"],
                                "cv_brier_default": card["confirm"]["default_mean"], **card["test_eval"]})
            mlflow.log_artifacts(model_dir, artifact_path="model")
    except Exception as e:                                    # tracking must never stop the run
        print(f"  (MLflow logging failed for {name}: {type(e).__name__}: {e})")


# ===================================================================== run
def run(args):
    os.makedirs(args.out, exist_ok=True)
    df = pd.read_csv(args.data, low_memory=False)
    with open(args.features, encoding="utf-8") as fh:
        feats = [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]
    miss = [c for c in ["repo", "pr_number", "y", "fold", "sample_weight", "created_at", "frames_split"] + feats
            if c not in df.columns]
    if miss:
        raise SystemExit("the training CSV is missing: " + ", ".join(miss[:10]) + " (re-run prepare_training_data.py)")
    man_path = os.path.join(os.path.dirname(os.path.abspath(args.data)), "manifest.json")
    manifest = json.load(open(man_path, encoding="utf-8")) if os.path.isfile(man_path) else {}
    extra = {"_created_s": te._seconds(df["created_at"]),
             "_closed_s": te._seconds(df["closed_at"]) if "closed_at" in df.columns else np.full(len(df), np.nan)}
    df = pd.concat([df, pd.DataFrame(extra, index=df.index)], axis=1)
    H = te.history_features(df, seed=args.seed)[0] if any(TUNERS[m].history for m in args.models) else pd.DataFrame(index=df.index)
    X = df[feats].apply(pd.to_numeric, errors="coerce").to_numpy(float)
    if args.tune_on == "train":
        use = (df["frames_split"] == "train").to_numpy()
        folds_use = te.repo_folds(df[use], 5, args.seed)
    else:
        use = np.ones(len(df), bool)
        folds_use = df["fold"].to_numpy()
    tr = (df["frames_split"] == "train").to_numpy()
    train_csv = os.path.join(args.out, "train_rows_for_eval.csv")
    df.loc[tr, [c for c in ("repo", "pr_number", "outcome_3class", "created_at", "closed_at") if c in df.columns]].to_csv(
        train_csv, index=False)
    data = dict(df=df, X=X, H=H, feats=feats, use=use, folds_use=folds_use,
                sha=(manifest.get("source") or {}).get("sha256"))
    ev = te.load_eval_module()
    line = "=" * 100
    print(f"{line}\nDEEP TUNING: {', '.join(args.models)}\n{line}")
    print(f"{len(df):,} PRs from {df['repo'].nunique()} repos, {len(feats)} features. Tuning on "
          f"{'all PRs' if args.tune_on == 'all' else 'the train repos only'}. Trials: "
          + ", ".join(f"{m} {args.trials or TRIALS[m]}" for m in args.models) + ".")
    rows = []
    for name in args.models:
        rows.append(tune_one(name, args, data, ev, train_csv))
    hist_card = os.path.join(REPO_ROOT, "data", "tuning", "model", "model_card.json")
    if os.path.isfile(hist_card):
        c = json.load(open(hist_card, encoding="utf-8"))
        t = c.get("test_eval", {})
        rows.append(dict(model="lgbm_history", cv_default=c["confirm"]["default_mean"], cv_tuned=c["confirm"]["tuned_mean"],
                         cv_gain=c["confirm"]["gain_mean"], better_on_all_splits=all(g > 0 for g in c["confirm"]["gain_per_split"]),
                         **{k: t.get(f"test_{k}") for k in ("brier", "brier_lo", "brier_hi", "skill", "calibration_error",
                                                            "auroc_nowhere", "accuracy", "checks_passed", "checks_failed")},
                         minutes=None))
    summ = pd.DataFrame(rows).sort_values("brier")
    summ.to_csv(os.path.join(args.out, "summary.csv"), index=False)
    print(f"\n{line}\nSUMMARY (every model tuned the same way; test = the team's evaluation on the 5 test repos)\n{line}")
    with pd.option_context("display.width", 230, "display.max_columns", 30, "display.float_format", "{:.4f}".format):
        print(summ.to_string(index=False))
    print("cv_default / cv_tuned: 5-fold cross-validation Brier over all PRs, mean of the reshuffled splits (lower is "
          "better). cv_gain above 0 = tuning helped; under about 0.003 it is too small to matter.")
    if args.tune_on == "all":
        print("The test repos also helped choose the settings, so the test numbers are slightly optimistic.")
    print(f"\nEverything is in {os.path.abspath(args.out)} (one folder per model, summary.csv)")


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], epilog=__doc__.split("\n\n", 1)[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", nargs="+", default=["lgbm", "logreg", "mlp_big", "mlp"], choices=list(TUNERS))
    ap.add_argument("--data", default=os.path.join(REPO_ROOT, "data", "training", "train_at_open.csv"))
    ap.add_argument("--features", default=os.path.join(REPO_ROOT, "data", "training", "features.txt"))
    ap.add_argument("--out", default=os.path.join(REPO_ROOT, "data", "tuning_models"))
    ap.add_argument("--trials", type=int, default=None,
                    help="trials per model (default: lgbm 40, logreg 15, mlp 25, mlp_big 25)")
    ap.add_argument("--confirm", type=int, default=3, help="reshuffled 5-fold splits used to confirm each winner")
    ap.add_argument("--seeds", type=int, default=5, help="seeds for the evaluation and final model (models with randomness)")
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
