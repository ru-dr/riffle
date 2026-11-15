#!/usr/bin/env python
"""
train_experiments.py - train two kinds of model on the clean training table and judge every one of them with the
team's evaluation script (pipelines/evaluation/eval_script.py), on the same train set and test set.

    python pipelines/training/train_experiments.py
    python pipelines/training/train_experiments.py --models logreg lgbm lgbm_history --fast
    python pipelines/training/train_experiments.py --cv --repeats 5

Two kinds of model
  per-row        each PR is scored from its own at-open features only:
                 base_overall (the training rows' outcome mix), logreg, lgbm (scikit-learn's gradient boosting when
                 lightgbm is not installed), mlp (a neural net with 3 layers: 512, 256, 128) and mlp_big (4 layers:
                 1024, 512, 256, 128), both with weight decay and early stopping
  previous rows  each PR is also scored from the PRs that came before it in the same repo, using only PRs that had
                 already closed when it opened, so nothing comes from the future:
                 base_repo_history (the repo's mix of earlier closed PRs), lgbm_history (lgbm plus history features:
                 the repo's recent outcome mix over several windows, its trend, how fast PRs close there, how many are
                 open and arriving now, and the author's earlier PRs in the repo)
  two-stage      two_stage and two_stage_history: stage 1 asks "will the PR land at all?", stage 2 "if it lands,
                 clean or after rework?" (trained on landed PRs only); the answers multiply into the 3 probabilities
  ensemble       ensemble: the average of --ensemble-members (default lgbm_history, two_stage_history, logreg) from
                 the same run, seed for seed and fold for fold; nothing extra is trained

Train and test   --split names the column that holds train/test: frames_split (default: frames.py's repo split, so
                 the test repos are never seen in training) or time_split (train on older PRs, test on newer ones).
                 Each model trains on the train rows (weighted with sample_weight; early stopping on a held-out repo
                 fold of the train rows, or the latest 15% of them for time_split) and predicts both sets.
Evaluation       eval_script.py runs on the train set and on the test set of every model, so every model gets exactly
                 the same metrics, charts and checklist the team uses to compare models. The train-vs-test gap shows
                 overfitting. --cv also runs 5-fold cross-validation by repo.
Repeats          --repeats N trains every model with N random seeds (models with no randomness train once). The
                 evaluation script gets the N runs stacked by seed: it averages them and reports how much they differ.
                 With --cv, each repeat also shuffles the repos into new folds. Results show the mean and the spread.

Writes to --out (default data/experiments at the repo root, which is gitignored):
  results.csv               one row per model: the evaluation script's test-set (and train-set) numbers
  comparison.png            test Brier and skill of every model
  <model>/predictions_train.csv, predictions_test.csv    in the format eval_script.py reads
  <model>/eval_train/, eval_test/                         the evaluation script's full report, tables and charts
  <model>/features_used.txt, feature_importance.csv (boosted models)
  history_features.csv      what each history feature means
Each model is also logged to MLflow when mlflow is installed.

Needs numpy, pandas, scikit-learn and matplotlib (the evaluation script draws charts); lightgbm is recommended.
"""
from __future__ import annotations

import argparse
import importlib.util
import io
import json
import math
import os
import sys
import time
import warnings

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
EVAL_SCRIPT = os.path.join(REPO_ROOT, "pipelines", "evaluation", "eval_script.py")
CLASSES = ("lands_clean", "lands_after_rework", "goes_nowhere")
SHORT = ("clean", "rework", "nowhere")
DAY = 86400.0
EXTRA_FOR_EVAL = ("created_at", "closed_at", "observed_through", "domain", "ai_agent_authored", "LA", "LD",
                  "repo_outside_closed_count", "repo_median_first_response_hours", "nowhere_reason")
HISTORY_MEANING = {
    "hist_repo_closed_before": "log(1 + PRs of the repo that had closed before this one opened)",
    "hist_repo_{o}_last{w}": "share of the repo's last {w} closed PRs that ended {o} (smoothed)",
    "hist_repo_nowhere_recent": "share of the repo's closed PRs that went nowhere, recent ones counting most "
                                "(half-life 60 days)",
    "hist_repo_nowhere_trend": "goes-nowhere share of the last 10 closed PRs minus that of the last 200",
    "hist_repo_days_to_close_last50": "log(1 + median days the repo's last 50 closed PRs stayed open)",
    "hist_repo_open_now": "log(1 + the repo's PRs open at the moment this one opened)",
    "hist_repo_opened_last30d": "log(1 + PRs opened in the repo in the 30 days before)",
    "hist_author_prev_prs": "log(1 + the author's earlier PRs in the repo)",
    "hist_author_prev_merged": "the author's earlier PRs in the repo that had merged by this one's opening",
    "hist_author_prev_nowhere": "the author's earlier PRs in the repo that had gone nowhere by this one's opening",
    "hist_author_nowhere_rate": "share of the author's earlier closed PRs that went nowhere (smoothed)",
    "hist_author_days_since_prev": "log(1 + days since the author's previous PR in the repo; blank = first PR)",
    "hist_author_open_now": "the author's other PRs open in the repo at that moment",
}


# ===================================================================== helpers
def _nanmean(a, axis=None):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        return np.nanmean(a, axis=axis)


def _times(s):
    try:
        t = pd.to_datetime(s, utc=True, errors="coerce", format="ISO8601")
    except (TypeError, ValueError):
        t = pd.to_datetime(s, utc=True, errors="coerce")
    if t.isna().all() and s.notna().any():
        t = pd.to_datetime(s, utc=True, errors="coerce")
    return t


def _seconds(s):
    t = _times(s)
    return (t - pd.Timestamp("1970-01-01", tz="UTC")).dt.total_seconds().to_numpy(dtype=float)


def one_hot(y, k=3):
    out = np.zeros((len(y), k))
    out[np.arange(len(y)), y] = 1.0
    return out


def brier_rows(P, y):
    return ((P - one_hot(y)) ** 2).sum(axis=1)


def group_mean(values, codes, n):
    s = np.bincount(codes, weights=values, minlength=n)
    c = np.bincount(codes, minlength=n)
    with np.errstate(invalid="ignore", divide="ignore"):
        return s / c


def auroc(yb, score):
    yb = np.asarray(yb, bool)
    n1 = int(yb.sum())
    n0 = len(yb) - n1
    if n1 == 0 or n0 == 0:
        return np.nan
    r = pd.Series(score).rank(method="average").to_numpy()
    return float((r[yb].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def _proba(model, X):
    """predict_proba with the columns in CLASSES order, even if a class was missing from training."""
    P = np.zeros((len(X), 3))
    raw = model.predict_proba(X)
    for j, c in enumerate(model.classes_):
        P[:, int(c)] = raw[:, j]
    s = P.sum(axis=1, keepdims=True)
    return np.where(s > 0, P / np.where(s > 0, s, 1), 1 / 3)


# ===================================================================== history features (previous rows)
def history_features(df, seed=0, windows=(10, 50, 200), smooth=5.0, half_life_days=60.0):
    """Features from the PRs that came before each PR in the same repo. Only PRs that had closed before it opened count
    as known outcomes; PRs still open at that moment only count as open. The PR's own outcome is never used.
    Returns the features and the raw counts the base_repo_history model needs."""
    n = len(df)
    created, closed = df["_created_s"].to_numpy(float), df["_closed_s"].to_numpy(float)
    y = df["y"].to_numpy()
    prior = np.full(3, 1 / 3)
    names = (["hist_repo_closed_before"] + [f"hist_repo_{o}_last{w}" for w in windows for o in SHORT]
             + ["hist_repo_nowhere_recent", "hist_repo_nowhere_trend", "hist_repo_days_to_close_last50",
                "hist_repo_open_now", "hist_repo_opened_last30d"])
    F = {k: np.full(n, np.nan) for k in names}
    counts_all = np.zeros((n, 3))
    tau = half_life_days * DAY / math.log(2)
    for _, idx in df.groupby("repo", sort=True).indices.items():
        c, cl, yy = created[idx], closed[idx], y[idx]
        end = np.where(np.isfinite(cl), cl, np.inf)
        order = np.argsort(end, kind="stable")
        end_s, y_s = end[order], yy[order]
        dur_s = (end - c)[order] / DAY
        k = np.searchsorted(end_s, np.where(np.isfinite(c), c, -np.inf), side="left")   # closed before opening
        cum = np.vstack([np.zeros(3), np.cumsum(one_hot(y_s), axis=0)])
        counts_all[idx] = cum[k]
        F["hist_repo_closed_before"][idx] = np.log1p(k)
        for w in windows:
            lo = np.maximum(0, k - w)
            cnt = cum[k] - cum[lo]
            rates = (cnt + smooth * prior) / ((k - lo)[:, None] + smooth)
            for j, o in enumerate(SHORT):
                F[f"hist_repo_{o}_last{w}"][idx] = rates[:, j]
        fin = np.isfinite(end_s)
        tmax = end_s[fin].max() if fin.any() else 0.0
        wts = np.where(fin, np.exp(np.clip((end_s - tmax) / tau, -700, 0)), 0.0)
        cw = np.concatenate([[0.0], np.cumsum(wts)])
        cwn = np.concatenate([[0.0], np.cumsum(wts * (y_s == 2))])
        with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
            scale = np.exp(np.clip((tmax - c) / tau, -700, 700))
            eff_n, eff_nw = cw[k] * scale, cwn[k] * scale
            F["hist_repo_nowhere_recent"][idx] = (eff_nw + smooth / 3) / (eff_n + smooth)
        F["hist_repo_nowhere_trend"][idx] = F["hist_repo_nowhere_last10"][idx] - F["hist_repo_nowhere_last200"][idx]
        med = np.full(len(idx), np.nan)
        for i, kk in enumerate(k):
            if kk:
                med[i] = np.median(dur_s[max(0, kk - 50):kk])
        F["hist_repo_days_to_close_last50"][idx] = np.log1p(np.clip(med, 0, None))
        c_sorted = np.sort(np.where(np.isfinite(c), c, np.inf))
        opened_before = np.searchsorted(c_sorted, c, side="left")
        F["hist_repo_open_now"][idx] = np.log1p(np.clip(opened_before - k, 0, None))
        F["hist_repo_opened_last30d"][idx] = np.log1p(opened_before - np.searchsorted(c_sorted, c - 30 * DAY, side="left"))
    A = author_history(df, created, closed, y, smooth)
    F.update(A)
    H = pd.DataFrame(F, index=df.index)
    return fill_unknown_authors(df, H, list(A), seed), counts_all


def author_history(df, created, closed, y, smooth):
    """The author's earlier PRs in the same repo (by the anonymous author_key)."""
    n = len(df)
    names = ["hist_author_prev_prs", "hist_author_prev_merged", "hist_author_prev_nowhere", "hist_author_nowhere_rate",
             "hist_author_days_since_prev", "hist_author_open_now"]
    F = {k: np.full(n, np.nan) for k in names}
    if "author_key" not in df.columns or df["author_key"].isna().all():
        return F
    keyed = df["author_key"].notna().to_numpy()
    groups = df[keyed].groupby(["repo", "author_key"], sort=False).indices
    pos = np.flatnonzero(keyed)
    for _, gi in groups.items():
        idx = pos[gi]
        c, cl, yy = created[idx], closed[idx], y[idx]
        end = np.where(np.isfinite(cl), cl, np.inf)
        o_end = np.argsort(end, kind="stable")
        end_s, y_s = end[o_end], yy[o_end]
        k = np.searchsorted(end_s, c, side="left")
        cum = np.vstack([np.zeros(3), np.cumsum(one_hot(y_s), axis=0)])
        cnt = cum[k]
        c_sorted = np.sort(c)
        before = np.searchsorted(c_sorted, c, side="left")
        F["hist_author_prev_prs"][idx] = np.log1p(before)
        F["hist_author_prev_merged"][idx] = cnt[:, 0] + cnt[:, 1]
        F["hist_author_prev_nowhere"][idx] = cnt[:, 2]
        F["hist_author_nowhere_rate"][idx] = (cnt[:, 2] + smooth / 3) / (k + smooth)
        prev = np.where(before > 0, c_sorted[np.maximum(before - 1, 0)], np.nan)
        F["hist_author_days_since_prev"][idx] = np.log1p(np.clip((c - prev) / DAY, 0, None))
        F["hist_author_open_now"][idx] = np.clip(before - k, 0, None)
    return F


def fill_unknown_authors(df, H, author_cols, seed):
    """PRs whose author could not be identified (no author_key) are mostly merged ones, so blank author features would
    give the outcome away. They get values drawn at random from the same repo's identified authors instead."""
    if "author_key" not in df.columns:
        return H
    unknown = df["author_key"].isna().to_numpy()
    if not unknown.any():
        return H
    rng = np.random.default_rng(seed + 11)
    H = H.copy()
    for col in author_cols:
        x = H[col].to_numpy(float).copy()
        known_all = x[~unknown & np.isfinite(x)]
        for _, idx in df.groupby("repo", sort=True).indices.items():
            holes = idx[unknown[idx]]
            if not len(holes):
                continue
            pool = x[idx][~unknown[idx] & np.isfinite(x[idx])]
            pool = pool if pool.size >= 20 else known_all
            if pool.size:
                x[holes] = rng.choice(pool, size=len(holes), replace=True)
        H[col] = x
    return H


# ===================================================================== models
class Model:
    kind = "per-row"
    history = False
    random = False          # True when the seed changes the trained model

    def __init__(self, args, seed=0):
        self.args = args
        self.seed = seed
        self.info = {}

    def fit_predict(self, tr, Xtr, te, Xte, val):
        """Train on (tr, Xtr), with val marking the training rows held out for early stopping; return probabilities
        for the rows of (te, Xte)."""
        raise NotImplementedError


class BaseOverall(Model):
    """The training rows' outcome mix, for every PR."""

    def fit_predict(self, tr, Xtr, te, Xte, val):
        w = tr["sample_weight"].to_numpy()
        mix = np.array([w[tr["y"].to_numpy() == k].sum() for k in range(3)])
        return np.tile(mix / mix.sum(), (len(te), 1))


class BaseRepoHistory(Model):
    """The repo's mix of earlier closed PRs, pulled toward the training mix while the repo has little history."""
    kind = "previous rows"

    def fit_predict(self, tr, Xtr, te, Xte, val):
        w = tr["sample_weight"].to_numpy()
        mix = np.array([w[tr["y"].to_numpy() == k].sum() for k in range(3)])
        mix = mix / mix.sum()
        cnt = te[["_hist_all_clean", "_hist_all_rework", "_hist_all_nowhere"]].to_numpy(float)
        return (cnt + 30 * mix) / (cnt.sum(axis=1, keepdims=True) + 30)


def _prep_fit(Xtr):
    """Median fill, log for long-tailed counts, standard scaling: learned on the training rows only."""
    med = np.nanmedian(Xtr, axis=0)
    med = np.where(np.isfinite(med), med, 0.0)
    filled = np.where(np.isnan(Xtr), med, Xtr)
    mn = filled.min(axis=0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        skew = pd.DataFrame(filled).skew().to_numpy()
    logcols = (mn >= 0) & (np.nan_to_num(skew) > 3)
    filled[:, logcols] = np.log1p(filled[:, logcols])
    mu, sd = filled.mean(axis=0), filled.std(axis=0)
    sd = np.where(sd > 0, sd, 1.0)
    return dict(med=med, logcols=logcols, mu=mu, sd=sd)


def _prep_apply(X, st):
    Z = np.where(np.isnan(X), st["med"], X)
    Z[:, st["logcols"]] = np.log1p(np.clip(Z[:, st["logcols"]], 0, None))
    return np.clip((Z - st["mu"]) / st["sd"], -10, 10)


class LogReg(Model):
    """Multinomial logistic regression with L2 regularization."""

    def fit_predict(self, tr, Xtr, te, Xte, val):
        from sklearn.linear_model import LogisticRegression
        st = _prep_fit(Xtr)
        m = LogisticRegression(C=0.5, max_iter=3000)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            m.fit(_prep_apply(Xtr, st), tr["y"].to_numpy(), sample_weight=tr["sample_weight"].to_numpy())
        return _proba(m, _prep_apply(Xte, st))


class MLP(Model):
    """A neural net: three hidden layers (512, 256, 128) and L2 weight decay. Trained one epoch at a time; stops when
    the log loss on the held-out repo fold has not improved for 5 epochs (at most 60) and keeps the best epoch."""
    random = True
    layers = (512, 256, 128)
    alpha = 3e-4
    max_epochs = 60
    patience = 5

    def fit_predict(self, tr, Xtr, te, Xte, val):
        import copy
        from sklearn.neural_network import MLPClassifier
        st = _prep_fit(Xtr[~val])
        Z, yv, w = _prep_apply(Xtr, st), tr["y"].to_numpy(), tr["sample_weight"].to_numpy()
        m = MLPClassifier(hidden_layer_sizes=self.layers, alpha=self.alpha, batch_size=256, learning_rate_init=1e-3,
                          random_state=self.seed)
        Zfit, yfit, wfit, Zval, yval, wval = Z[~val], yv[~val], w[~val], Z[val], yv[val], w[val]
        best, best_weights, bad, epochs = np.inf, None, 0, 0
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            for epochs in range(1, self.max_epochs + 1):
                try:
                    m.partial_fit(Zfit, yfit, sample_weight=wfit, classes=np.arange(3))
                except TypeError:                               # scikit-learn before 1.7 has no MLP weights
                    m.partial_fit(Zfit, yfit, classes=np.arange(3))
                    self.info["note"] = "trained without sample_weight (old scikit-learn)"
                P = _proba(m, Zval)
                loss = float(np.average(-np.log(np.clip(P[np.arange(len(yval)), yval], 1e-15, 1)), weights=wval))
                if loss < best - 1e-4:
                    best, best_weights, bad = loss, copy.deepcopy((m.coefs_, m.intercepts_)), 0
                else:
                    bad += 1
                    if bad >= self.patience:
                        break
        m.coefs_, m.intercepts_ = best_weights
        self.info.setdefault("epochs", []).append(epochs)
        return _proba(m, _prep_apply(Xte, st))


class MLPBig(MLP):
    """A wider, deeper neural net: four hidden layers (1024, 512, 256, 128), stronger weight decay."""
    layers = (1024, 512, 256, 128)
    alpha = 1e-3


class GBM(Model):
    """Gradient-boosted trees: lightgbm when installed, else scikit-learn's HistGradientBoosting. Early stopping on the
    held-out training rows, then a refit on all training rows with that many rounds."""
    random = True

    def fit_predict(self, tr, Xtr, te, Xte, val):
        y, w = tr["y"].to_numpy(), tr["sample_weight"].to_numpy()
        fast = self.args.fast
        try:
            import lightgbm as lgb
        except ImportError:
            lgb = None
        if lgb is not None:
            params = dict(objective="multiclass", learning_rate=0.08 if fast else 0.03, num_leaves=31,
                          min_child_samples=100, subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
                          reg_lambda=1.0, n_estimators=1000 if fast else 3000, random_state=self.seed, verbose=-1)
            m = lgb.LGBMClassifier(**params)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                m.fit(Xtr[~val], y[~val], sample_weight=w[~val], eval_set=[(Xtr[val], y[val])],
                      eval_sample_weight=[w[val]], callbacks=[lgb.early_stopping(100, verbose=False)])
                rounds = int(m.best_iteration_ or params["n_estimators"])
                final = lgb.LGBMClassifier(**{**params, "n_estimators": max(50, int(rounds * 1.1))})
                final.fit(Xtr, y, sample_weight=w)
            self.info.setdefault("rounds", []).append(rounds)
            self.info.setdefault("gain", []).append(final.booster_.feature_importance("gain"))
            self.info["library"] = "lightgbm"
            return _proba(final, Xte)
        from sklearn.ensemble import HistGradientBoostingClassifier
        params = dict(learning_rate=0.1 if fast else 0.05, max_leaf_nodes=31, min_samples_leaf=100,
                      l2_regularization=1.0, max_iter=500 if fast else 1500, random_state=self.seed)
        self.info["library"] = "scikit-learn HistGradientBoosting (lightgbm not installed)"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                m = HistGradientBoostingClassifier(early_stopping=True, n_iter_no_change=50, **params)
                m.fit(Xtr[~val], y[~val], sample_weight=w[~val], X_val=Xtr[val], y_val=y[val], sample_weight_val=w[val])
                rounds = int(m.n_iter_)
                final = HistGradientBoostingClassifier(early_stopping=False, **{**params, "max_iter": max(50, int(rounds * 1.1))})
                final.fit(Xtr, y, sample_weight=w)
            except TypeError:                                   # scikit-learn before 1.6: no X_val
                final = HistGradientBoostingClassifier(early_stopping=True, validation_fraction=0.1, n_iter_no_change=50,
                                                       **params)
                final.fit(Xtr, y, sample_weight=w)
                rounds = int(final.n_iter_)
        self.info.setdefault("rounds", []).append(rounds)
        return _proba(final, Xte)


class GBMHistory(GBM):
    kind = "previous rows"
    history = True


class TwoStage(Model):
    """Two yes/no questions instead of one three-way choice. Stage 1: will the PR land at all (lands vs goes
    nowhere), trained on every PR. Stage 2: if it lands, clean or after rework, trained only on PRs that landed.
    Combined: P(nowhere) = 1 - P(lands); P(clean) = P(lands) * P(clean | lands); P(rework) = P(lands) * (1 - that).
    Each stage is a gradient-boosted model with early stopping on the held-out rows, then a refit."""
    random = True

    def _binary(self, Xtr, yb, w, val, Xte, stage):
        """P(yes) for the rows of Xte from a yes/no gradient-boosted model."""
        fast = self.args.fast
        try:
            import lightgbm as lgb
        except ImportError:
            lgb = None
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            if lgb is not None:
                params = dict(objective="binary", learning_rate=0.08 if fast else 0.03, num_leaves=31,
                              min_child_samples=100, subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
                              reg_lambda=1.0, n_estimators=1000 if fast else 3000, random_state=self.seed, verbose=-1)
                m = lgb.LGBMClassifier(**params)
                m.fit(Xtr[~val], yb[~val], sample_weight=w[~val], eval_set=[(Xtr[val], yb[val])],
                      eval_sample_weight=[w[val]], callbacks=[lgb.early_stopping(100, verbose=False)])
                rounds = int(m.best_iteration_ or params["n_estimators"])
                final = lgb.LGBMClassifier(**{**params, "n_estimators": max(50, int(rounds * 1.1))})
                final.fit(Xtr, yb, sample_weight=w)
                self.info.setdefault(f"gain_stage{stage}", []).append(final.booster_.feature_importance("gain"))
                self.info["library"] = "lightgbm"
            else:
                from sklearn.ensemble import HistGradientBoostingClassifier
                params = dict(learning_rate=0.1 if fast else 0.05, max_leaf_nodes=31, min_samples_leaf=100,
                              l2_regularization=1.0, max_iter=500 if fast else 1500, random_state=self.seed)
                self.info["library"] = "scikit-learn HistGradientBoosting (lightgbm not installed)"
                try:
                    m = HistGradientBoostingClassifier(early_stopping=True, n_iter_no_change=50, **params)
                    m.fit(Xtr[~val], yb[~val], sample_weight=w[~val], X_val=Xtr[val], y_val=yb[val],
                          sample_weight_val=w[val])
                    rounds = int(m.n_iter_)
                    final = HistGradientBoostingClassifier(early_stopping=False,
                                                           **{**params, "max_iter": max(50, int(rounds * 1.1))})
                    final.fit(Xtr, yb, sample_weight=w)
                except TypeError:                           # scikit-learn before 1.6: no X_val
                    final = HistGradientBoostingClassifier(early_stopping=True, validation_fraction=0.1,
                                                           n_iter_no_change=50, **params)
                    final.fit(Xtr, yb, sample_weight=w)
                    rounds = int(final.n_iter_)
        self.info.setdefault(f"rounds_stage{stage}", []).append(rounds)
        classes = list(final.classes_)
        return final.predict_proba(Xte)[:, classes.index(1)] if 1 in classes else np.zeros(len(Xte))

    def fit_predict(self, tr, Xtr, te, Xte, val):
        y, w = tr["y"].to_numpy(), tr["sample_weight"].to_numpy()
        p_lands = self._binary(Xtr, (y != 2).astype(int), w, val, Xte, 1)
        landed = y != 2
        p_clean = self._binary(Xtr[landed], (y[landed] == 0).astype(int), w[landed], val[landed], Xte, 2)
        P = np.column_stack([p_lands * p_clean, p_lands * (1 - p_clean), 1 - p_lands])
        P = np.clip(P, 1e-6, 1)
        return P / P.sum(axis=1, keepdims=True)


class TwoStageHistory(TwoStage):
    kind = "previous rows"
    history = True


class Ensemble(Model):
    """Not trained: the average of other models' probabilities from the same run (--ensemble-members), seed for seed
    and fold for fold."""
    kind = "ensemble"


MODELS = {"base_overall": BaseOverall, "logreg": LogReg, "lgbm": GBM, "mlp": MLP, "mlp_big": MLPBig,
          "two_stage": TwoStage, "base_repo_history": BaseRepoHistory, "lgbm_history": GBMHistory,
          "two_stage_history": TwoStageHistory, "ensemble": Ensemble}


# ===================================================================== train / test and the team's evaluation
def split_masks(df, args):
    if args.split not in df.columns:
        raise SystemExit(f"the training CSV has no {args.split} column (re-run prepare_training_data.py)")
    tr = (df[args.split] == "train").to_numpy()
    te = (df[args.split] == "test").to_numpy()
    if tr.sum() < 500 or te.sum() < 200:
        raise SystemExit(f"{args.split}: {tr.sum():,} train and {te.sum():,} test PRs is too few")
    if args.split == "time_split":
        cut = np.quantile(df.loc[tr, "_created_s"], 0.85)
        val = (df.loc[tr, "_created_s"] >= cut).to_numpy()            # the latest 15% of the training period
        how = "the latest 15% of the training rows"
    else:
        val_fold = df.loc[tr, "fold"].value_counts().idxmax()
        val = (df.loc[tr, "fold"] == val_fold).to_numpy()             # a whole repo fold of the training rows
        how = f"repo fold {val_fold} of the training rows"
    return tr, te, val, how


def load_eval_module():
    if not os.path.isfile(EVAL_SCRIPT):
        raise SystemExit(f"cannot find the evaluation script at {EVAL_SCRIPT}")
    spec = importlib.util.spec_from_file_location("riffle_eval_script", EVAL_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod                 # dataclasses look the module up while it loads
    spec.loader.exec_module(mod)
    return mod


def run_eval(ev, pred_csv, name, out_dir, train_csv, features_file, boot):
    """The team's evaluation script on one predictions file (its long report goes to out_dir/report.txt)."""
    argv = ["--csv", pred_csv, "--model", name, "--out", out_dir, "--train", train_csv,
            "--features-used", features_file, "--boot", str(boot)]
    old, buf = sys.stdout, io.StringIO()
    try:
        sys.stdout = buf
        ev.main(argv)
    finally:
        sys.stdout = old
    with open(os.path.join(out_dir, "summary.json"), encoding="utf-8") as fh:
        return json.load(fh)


def eval_numbers(summ, prefix):
    """The comparison numbers from one evaluation summary."""
    m = summ.get("metrics") or {}
    q = (summ.get("review_queue") or {}).get("model") or {}
    checks = summ.get("checklist") or []
    seeds = summ.get("seeds") or {}
    keys = ("brier", "brier_lo", "brier_hi", "skill", "skill_lo", "skill_hi", "rps", "log_loss", "calibration_error",
            "calibration_error_corrected", "auroc_clean", "auroc_rework", "auroc_nowhere", "auroc_average",
            "macro_f1", "balanced_accuracy", "accuracy")
    out = {f"{prefix}{k}": m.get(k) for k in keys}
    out[f"{prefix}nowhere_top5_new_prs"] = q.get("p_at_5_new")
    out[f"{prefix}review_next_top10"] = q.get("ndcg_at_10")
    out[f"{prefix}checks_passed"] = sum(c.get("result") == "PASS" for c in checks)
    out[f"{prefix}checks_failed"] = sum(c.get("result") == "FAIL" for c in checks)
    out[f"{prefix}base_rate_used"] = summ.get("base_rate_used")
    out[f"{prefix}one_run_brier"] = seeds.get("brier_average")       # a single seed's score, averaged over seeds
    out[f"{prefix}one_run_brier_spread"] = seeds.get("brier_spread")
    out[f"{prefix}top_guess_changes_between_runs"] = seeds.get("top_guess_changes")
    return {k: (np.nan if v is None else v) for k, v in out.items()}


def write_predictions(path, df, Ps, seeds):
    """One block of rows per seed (a seed column when there are several), as eval_script.py reads them."""
    cols = ["repo", "pr_number", "outcome_3class"] + [c for c in EXTRA_FOR_EVAL if c in df.columns]
    blocks = []
    for P, s in zip(Ps, seeds):
        b = df[cols].copy()
        b["p_clean"], b["p_rework"], b["p_nowhere"] = P[:, 0], P[:, 1], P[:, 2]
        if len(Ps) > 1:
            b["seed"] = s
        blocks.append(b)
    pd.concat(blocks, ignore_index=True).to_csv(path, index=False)


# ===================================================================== optional repo cross-validation
def repo_folds(df, k, seed):
    """Repos in a random order, each put in the fold with the fewest PRs so far: a different, still balanced split of
    the repos for each seed."""
    rng = np.random.default_rng(seed)
    sizes = df["repo"].value_counts()
    repos = list(sizes.index)
    rng.shuffle(repos)
    totals, assign = [0] * k, {}
    for r in repos:
        f = int(np.argmin(totals))
        assign[r] = f
        totals[f] += int(sizes[r])
    return df["repo"].map(assign).to_numpy()


def cross_validate(name, df, X, args, folds, seed):
    """5-fold cross-validation by repo with the given fold of each row: repo-averaged Brier and skill of the
    out-of-fold predictions."""
    ids = sorted(np.unique(folds))
    P = np.full((len(df), 3), np.nan)
    Pb = np.full((len(df), 3), np.nan)
    for i, f in enumerate(ids):
        te = folds == f
        tr = ~te
        val = folds[tr] == ids[(i + 1) % len(ids)]
        P[te] = MODELS[name](args, seed).fit_predict(df[tr], X[tr], df[te], X[te], val)
        Pb[te] = BaseOverall(args, seed).fit_predict(df[tr], X[tr], df[te], X[te], val)
    b, s = oof_scores(df, P, Pb)
    return b, s, P, Pb


def oof_scores(df, P, Pb):
    """Repo-averaged Brier of out-of-fold predictions, and skill against the training mix's predictions Pb."""
    codes = pd.Categorical(df["repo"]).codes
    R = int(codes.max()) + 1
    y = df["y"].to_numpy()
    per, per_b = group_mean(brier_rows(P, y), codes, R), group_mean(brier_rows(Pb, y), codes, R)
    return float(_nanmean(per)), float(1 - _nanmean(per) / _nanmean(per_b))


def run_ensemble(args, df, tr, te, preds, cv_store, ev, train_csv, manifest):
    """The ensemble: the members' probabilities averaged, run i of the ensemble from run i of each member (a member
    with no randomness repeats its one run), and the same for each cross-validation repeat. Judged like any model."""
    members = args.ensemble_members
    mdir = os.path.join(args.out, "ensemble")
    os.makedirs(mdir, exist_ok=True)
    names = list(dict.fromkeys(f for m in members for f in preds[m][2]))
    feat_file = os.path.join(mdir, "features_used.txt")
    with open(feat_file, "w", encoding="utf-8") as fh:
        fh.write("\n".join(names) + "\n")
    print(f"\n  ensemble (average of {', '.join(members)}) ...", flush=True)
    n = max(len(preds[m][1]) for m in members)
    Ps = [np.mean([preds[m][1][i if i < len(preds[m][1]) else 0] for m in members], axis=0) for i in range(n)]
    seeds = [args.seed + i for i in range(n)]
    write_predictions(os.path.join(mdir, "predictions_test.csv"), df[te], [P[te] for P in Ps], seeds)
    write_predictions(os.path.join(mdir, "predictions_train.csv"), df[tr], [P[tr] for P in Ps], seeds)
    rec = dict(model="ensemble", kind="ensemble", features=len(names), runs=n, train_seconds=0.0,
               members="+".join(members))
    print("     evaluating the test set ...", flush=True)
    rec.update(eval_numbers(run_eval(ev, os.path.join(mdir, "predictions_test.csv"), "ensemble",
                                     os.path.join(mdir, "eval_test"), train_csv, feat_file, args.boot), "test_"))
    if not args.no_train_eval:
        print("     evaluating the train set ...", flush=True)
        rec.update(eval_numbers(run_eval(ev, os.path.join(mdir, "predictions_train.csv"), "ensemble",
                                         os.path.join(mdir, "eval_train"), train_csv, feat_file, args.boot), "train_"))
        rec["overfit_gap"] = rec["test_brier"] - rec["train_brier"]
    if args.cv and all(m in cv_store for m in members):
        cvb, cvs = [], []
        for r in range(min(len(cv_store[m]) for m in members)):
            P = np.mean([cv_store[m][r][0] for m in members], axis=0)
            b, s_ = oof_scores(df, P, cv_store[members[0]][r][1])
            cvb.append(b)
            cvs.append(s_)
        rec.update(cv_brier=float(np.mean(cvb)), cv_brier_spread=float(np.std(cvb, ddof=1)) if len(cvb) > 1 else 0.0,
                   cv_skill=float(np.mean(cvs)), cv_skill_spread=float(np.std(cvs, ddof=1)) if len(cvs) > 1 else 0.0,
                   cv_runs=len(cvb))
    logged = log_mlflow(args, "ensemble", "ensemble", {k: v for k, v in rec.items() if k.startswith(("test_", "train_", "cv_"))},
                        {"library": "average of " + "+".join(members)}, len(names), manifest)
    print(f"     test Brier {rec['test_brier']:.4f} [{rec['test_brier_lo']:.4f}, {rec['test_brier_hi']:.4f}], "
          f"skill {rec['test_skill']:+.3f}, checks {rec['test_checks_passed']} passed / {rec['test_checks_failed']} failed"
          + (f", train Brier {rec['train_brier']:.4f}" if "train_brier" in rec else "")
          + (f", cv Brier {rec['cv_brier']:.4f} +/- {rec['cv_brier_spread']:.4f}" if "cv_brier" in rec else "")
          + f"{'  (MLflow)' if logged else ''}")
    return rec


# ===================================================================== MLflow and chart
def log_mlflow(args, name, kind, metrics, info, n_features, manifest):
    try:
        import mlflow
    except ImportError:
        return False
    try:
        mlflow.set_experiment(args.mlflow_experiment)
        with mlflow.start_run(run_name=name):
            mlflow.log_params(dict(model=name, kind=kind, features=n_features, fast=args.fast, seed=args.seed,
                                   repeats=args.repeats, split=args.split, library=info.get("library", "scikit-learn"),
                                   data_sha256=(manifest or {}).get("source", {}).get("sha256", "?")[:16]))
            mlflow.log_metrics({k: float(v) for k, v in metrics.items()
                                if isinstance(v, (int, float, np.integer, np.floating)) and np.isfinite(v)})
        return True
    except Exception as e:                                    # tracking must never stop an experiment
        print(f"  (MLflow logging failed for {name}: {type(e).__name__}: {e})")
        return False


def chart(path, res):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None
    r = res.sort_values("test_brier", ascending=False).reset_index(drop=True)
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(13, 1.2 + 0.55 * len(r)))
    for i, row in r.iterrows():
        color = {"per-row": "#1f77b4", "previous rows": "#ff7f0e", "ensemble": "#2ca02c"}.get(row["kind"], "#7f7f7f")
        a1.errorbar(row["test_brier"], i, fmt="o", color=color, capsize=4,
                    xerr=[[np.nan_to_num(row["test_brier"] - row["test_brier_lo"])],
                          [np.nan_to_num(row["test_brier_hi"] - row["test_brier"])]])
        a1.scatter(row["train_brier"], i, marker="x", color=color, s=30) if np.isfinite(row.get("train_brier", np.nan)) else None
        a2.barh(i, row["test_skill"], color=color, capsize=4,
                xerr=[[np.nan_to_num(row["test_skill"] - row["test_skill_lo"])],
                      [np.nan_to_num(row["test_skill_hi"] - row["test_skill"])]])
    for a in (a1, a2):
        a.set_yticks(range(len(r)))
        a.set_yticklabels([f"{m} ({k})" for m, k in zip(r["model"], r["kind"])], fontsize=8)
    a1.set_xlabel("Brier, averaged per repo (lower is better). dot = test set with 95% range, x = train set")
    a2.axvline(0, color="k", lw=1)
    a2.set_xlabel("test-set skill vs the base rate (higher is better)")
    a1.set_title("Evaluation script on every model: blue = per-row, orange = previous rows, green = ensemble")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


# ===================================================================== run
def run(args):
    os.makedirs(args.out, exist_ok=True)
    ev = load_eval_module()
    df = pd.read_csv(args.data, low_memory=False)
    with open(args.features, encoding="utf-8") as fh:
        feats = [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]
    need = ["repo", "pr_number", "outcome_3class", "y", "fold", "sample_weight", "created_at", args.split]
    miss = [c for c in need + feats if c not in df.columns]
    if miss:
        raise SystemExit("the training CSV is missing: " + ", ".join(miss[:10]) + " (re-run prepare_training_data.py)")
    man_path = os.path.join(os.path.dirname(os.path.abspath(args.data)), "manifest.json")
    manifest = json.load(open(man_path, encoding="utf-8")) if os.path.isfile(man_path) else None
    extra = {"_created_s": _seconds(df["created_at"]),
             "_closed_s": _seconds(df["closed_at"]) if "closed_at" in df.columns else np.full(len(df), np.nan)}
    df = pd.concat([df, pd.DataFrame(extra, index=df.index)], axis=1)
    X = df[feats].apply(pd.to_numeric, errors="coerce").to_numpy(float)
    H = None
    if any(MODELS[m].kind == "previous rows" for m in args.models):
        H, counts_all = history_features(df, seed=args.seed)
        df = pd.concat([df, pd.DataFrame(counts_all, index=df.index,
                                         columns=["_hist_all_clean", "_hist_all_rework", "_hist_all_nowhere"])], axis=1)
        pd.DataFrame([dict(feature=k, meaning=v) for k, v in HISTORY_MEANING.items()]).to_csv(
            os.path.join(args.out, "history_features.csv"), index=False)
    tr, te, val, val_how = split_masks(df, args)
    train_csv = os.path.join(args.out, "train_rows_for_eval.csv")
    df.loc[tr, [c for c in ("repo", "pr_number", "outcome_3class", "created_at", "closed_at") if c in df.columns]].to_csv(
        train_csv, index=False)

    line = "=" * 100
    print(f"{line}\nMODEL EXPERIMENTS\n{line}")
    print(f"{len(df):,} PRs from {df['repo'].nunique()} repos, {len(feats)} features"
          + (f" (+{H.shape[1]} history features for the previous-rows models)" if H is not None else "") + ".")
    print(f"Split {args.split}: {tr.sum():,} train PRs ({df.loc[tr, 'repo'].nunique()} repos), {te.sum():,} test PRs "
          f"({df.loc[te, 'repo'].nunique()} repos). Early stopping on {val_how}.")
    if args.split == "frames_split":
        print("Test repos: " + ", ".join(sorted(df.loc[te, "repo"].unique())))
    if H is not None:
        y = df["y"].to_numpy()
        risky = []
        for c in H.columns:
            v = H[c].to_numpy(float)
            ok = np.isfinite(v)
            if ok.sum() >= 200 and np.unique(v[ok]).size > 1:
                best = max(max(a, 1 - a) for a in (auroc(y[ok] == k, v[ok]) for k in range(3)) if np.isfinite(a))
                if best > 0.85:
                    risky.append(f"{c} ({best:.3f})")
        print("History leak check: " + ("these predict too well on their own, look before trusting them: "
                                        + ", ".join(risky) if risky else "no history feature predicts the outcome "
                                        "suspiciously well on its own."))
    print("Every model is judged by pipelines/evaluation/eval_script.py on the train set and on the test set.")

    results, importance = [], []
    preds, cv_store = {}, {}                 # each model's runs and cross-validation predictions, for the ensemble
    order = [m for m in args.models if m != "ensemble"] + (["ensemble"] if "ensemble" in args.models else [])
    for name in order:
        if name == "ensemble":
            results.append(run_ensemble(args, df, tr, te, preds, cv_store, ev, train_csv, manifest))
            continue
        cls = MODELS[name]
        Xm = np.hstack([X, H.to_numpy(float)]) if cls.history else X
        names = feats + (list(H.columns) if cls.history else [])
        mdir = os.path.join(args.out, name)
        os.makedirs(mdir, exist_ok=True)
        feat_file = os.path.join(mdir, "features_used.txt")
        with open(feat_file, "w", encoding="utf-8") as fh:
            fh.write("\n".join(names) + "\n")
        print(f"\n  {name} ({cls.kind}): training ...", flush=True)
        seeds = [args.seed + r for r in range(args.repeats)] if cls.random else [args.seed]
        Ps, infos, secs = [], [], 0.0
        for s in seeds:
            t0 = time.time()
            model = cls(args, s)
            Ps.append(model.fit_predict(df[tr], Xm[tr], df, Xm, val))     # one model, predictions for every row
            secs += time.time() - t0
            infos.append(model.info)
        preds[name] = (seeds, Ps, names)
        if len(seeds) > 1:
            print(f"     trained {len(seeds)} times (seeds {seeds[0]}-{seeds[-1]})", flush=True)
        write_predictions(os.path.join(mdir, "predictions_test.csv"), df[te], [P[te] for P in Ps], seeds)
        write_predictions(os.path.join(mdir, "predictions_train.csv"), df[tr], [P[tr] for P in Ps], seeds)
        print("     evaluating the test set ...", flush=True)
        rec = dict(model=name, kind=cls.kind, features=len(names), runs=len(seeds), train_seconds=round(secs, 1))
        rec.update(eval_numbers(run_eval(ev, os.path.join(mdir, "predictions_test.csv"), name,
                                         os.path.join(mdir, "eval_test"), train_csv, feat_file, args.boot), "test_"))
        if not args.no_train_eval:
            print("     evaluating the train set ...", flush=True)
            rec.update(eval_numbers(run_eval(ev, os.path.join(mdir, "predictions_train.csv"), name,
                                             os.path.join(mdir, "eval_train"), train_csv, feat_file, args.boot), "train_"))
            rec["overfit_gap"] = rec["test_brier"] - rec["train_brier"]
        if args.cv:
            cvb, cvs = [], []
            for r in range(args.repeats):
                print(f"     repo cross-validation {r + 1} of {args.repeats} ...", flush=True)
                folds = df["fold"].to_numpy() if r == 0 else repo_folds(df, df["fold"].nunique(), args.seed + 100 + r)
                b, s_, P_oof, P_base = cross_validate(name, df, Xm, args, folds, args.seed + r)
                cv_store.setdefault(name, []).append((P_oof, P_base))
                cvb.append(b)
                cvs.append(s_)
            rec.update(cv_brier=float(np.mean(cvb)), cv_brier_spread=float(np.std(cvb, ddof=1)) if len(cvb) > 1 else 0.0,
                       cv_skill=float(np.mean(cvs)), cv_skill_spread=float(np.std(cvs, ddof=1)) if len(cvs) > 1 else 0.0,
                       cv_runs=len(cvb))
        info = infos[0]
        if info.get("library"):
            rec["library"] = info["library"]
        rounds = [x for i in infos for x in i.get("rounds", [])]
        if rounds:
            rec["boosting_rounds"] = int(np.mean(rounds))
        epochs = [x for i in infos for x in i.get("epochs", [])]
        if epochs:
            rec["epochs"] = int(np.mean(epochs))
        if info.get("note"):
            rec["note"] = info["note"]
        gains = [g for i in infos for g in i.get("gain", [])]
        if gains:
            g = np.mean(gains, axis=0)
            imp = pd.DataFrame({"feature": names, "gain_share": g / g.sum()}).sort_values("gain_share", ascending=False)
            imp.to_csv(os.path.join(mdir, "feature_importance.csv"), index=False)
            importance.append((name, imp))
        stage_imp = {}
        for st_, label in ((1, "stage 1: will it land?"), (2, "stage 2: clean or rework, if it lands?")):
            r_ = [x for i in infos for x in i.get(f"rounds_stage{st_}", [])]
            if r_:
                rec[f"rounds_stage{st_}"] = int(np.mean(r_))
            g_ = [g for i in infos for g in i.get(f"gain_stage{st_}", [])]
            if g_:
                g = np.mean(g_, axis=0)
                stage_imp[f"stage{st_}_gain_share"] = g / g.sum()
                imp = pd.DataFrame({"feature": names, "gain_share": g / g.sum()}).sort_values("gain_share", ascending=False)
                importance.append((f"{name} {label}", imp))
        if stage_imp:
            pd.DataFrame({"feature": names, **stage_imp}).to_csv(os.path.join(mdir, "feature_importance.csv"), index=False)
        results.append(rec)
        logged = log_mlflow(args, name, cls.kind, {k: v for k, v in rec.items() if k.startswith(("test_", "train_", "cv_"))},
                            info, len(names), manifest)
        print(f"     test Brier {rec['test_brier']:.4f} [{rec['test_brier_lo']:.4f}, {rec['test_brier_hi']:.4f}], "
              f"skill {rec['test_skill']:+.3f}, checks {rec['test_checks_passed']} passed / {rec['test_checks_failed']} failed"
              + (f", train Brier {rec['train_brier']:.4f}" if "train_brier" in rec else "")
              + (f", cv Brier {rec['cv_brier']:.4f} +/- {rec['cv_brier_spread']:.4f}" if "cv_brier" in rec else "")
              + f"  ({secs:.0f}s training{', MLflow' if logged else ''})")
    res = pd.DataFrame(results)
    res.to_csv(os.path.join(args.out, "results.csv"), index=False)
    png = chart(os.path.join(args.out, "comparison.png"), res)
    report(res, importance, args, png)


def report(res, importance, args, png):
    line = "=" * 100
    print(f"\n{line}\nRESULTS ON THE TEST SET (the team's evaluation script; test repos were never seen in training)\n{line}")
    cols = ["model", "kind", "runs", "test_brier", "test_brier_lo", "test_brier_hi", "test_one_run_brier_spread",
            "test_skill", "test_calibration_error", "test_auroc_nowhere", "test_auroc_average",
            "test_nowhere_top5_new_prs", "test_review_next_top10", "test_checks_passed", "test_checks_failed",
            "train_brier", "overfit_gap", "cv_brier", "cv_brier_spread", "cv_skill", "cv_skill_spread"]
    names = {"test_brier": "brier", "test_brier_lo": "lo", "test_brier_hi": "hi",
             "test_one_run_brier_spread": "run_spread", "test_skill": "skill",
             "test_calibration_error": "calib_err", "test_auroc_nowhere": "auroc_nowhere",
             "test_auroc_average": "auroc_avg", "test_nowhere_top5_new_prs": "nowhere_top5_new",
             "test_review_next_top10": "review_top10", "test_checks_passed": "pass", "test_checks_failed": "fail",
             "train_brier": "train_brier", "overfit_gap": "gap", "cv_brier_spread": "cv_spread",
             "cv_skill_spread": "cv_skill_spread"}
    show = res.sort_values("test_brier")[[c for c in cols if c in res.columns]].rename(columns=names)
    with pd.option_context("display.width", 230, "display.max_columns", 30, "display.float_format", "{:.4f}".format):
        print(show.to_string(index=False))
    used = res["test_base_rate_used"].dropna().unique() if "test_base_rate_used" in res.columns else []
    print("brier: 0 = perfect, 2 = worst, averaged per repo, with a 95% range over test repos (for several runs: the "
          "runs' averaged predictions). run_spread: how much one run's test Brier varies between seeds. skill: against "
          + (used[0] if len(used) else "the base rate") + " (above 0 = better than guessing). gap: test minus train "
          "Brier (a big gap = the model memorised the training repos). pass/fail: the evaluation checklist. cv: repo "
          "cross-validation over all repos, mean and spread over the repeats.")
    if "library" in res.columns and res["library"].notna().any():
        print("Boosting library: " + ", ".join(sorted(res["library"].dropna().unique())))
    learned = res[~res["model"].str.startswith("base_")]
    for k, g in learned.groupby("kind"):
        r = g.sort_values("test_brier").iloc[0]
        print(f"Best {k} model: {r['model']} (test Brier {r['test_brier']:.4f}, skill {r['test_skill']:+.3f})")
    if {"lgbm", "lgbm_history"} <= set(res["model"]):
        a = res.set_index("model")
        d = a.loc["lgbm", "test_brier"] - a.loc["lgbm_history", "test_brier"]
        print(f"Adding the previous-rows features to lgbm changes the test Brier by {-d:+.4f} "
              f"({'better' if d > 0 else 'worse'}; differences under about 0.005 are noise).")
    a = res.set_index("model")
    for two, one in (("two_stage_history", "lgbm_history"), ("two_stage", "lgbm")):
        if {two, one} <= set(res["model"]):
            d = a.loc[one, "test_brier"] - a.loc[two, "test_brier"]
            cv = (f"; cross-validation {a.loc[one, 'cv_brier'] - a.loc[two, 'cv_brier']:+.4f}"
                  if "cv_brier" in a.columns and np.isfinite(a.loc[two, "cv_brier"]) else "")
            print(f"{two} vs {one}: test Brier {'better' if d > 0 else 'worse'} by {abs(d):.4f}{cv} (positive = two-stage "
                  "better; under about 0.005 is noise).")
    if "ensemble" in a.index:
        mem = [m for m in args.ensemble_members if m in a.index]
        best_m = min(mem, key=lambda m: a.loc[m, "test_brier"])
        d = a.loc[best_m, "test_brier"] - a.loc["ensemble", "test_brier"]
        print(f"ensemble vs its best member ({best_m}): test Brier {'better' if d > 0 else 'worse'} by {abs(d):.4f}.")
    for name, imp in importance:
        print(f"\nTop 15 features of {name} (share of the model's gain):")
        with pd.option_context("display.float_format", "{:.4f}".format):
            print(imp.head(15).to_string(index=False))
    print(f"\n{line}\nFILES\n{line}")
    print(f"  {os.path.join(args.out, 'results.csv')}" + (f" and {os.path.basename(png)}" if png else ""))
    print(f"  {os.path.join(args.out, '<model>', 'eval_test', 'report.txt')}: each model's full evaluation report "
          "(eval_train for the train set), with its charts")


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], epilog=__doc__.split("\n\n", 1)[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=os.path.join(REPO_ROOT, "data", "training", "train_at_open.csv"))
    ap.add_argument("--features", default=os.path.join(REPO_ROOT, "data", "training", "features.txt"))
    ap.add_argument("--out", default=os.path.join(REPO_ROOT, "data", "experiments"))
    ap.add_argument("--models", nargs="+", default=list(MODELS), choices=list(MODELS), help="models to train")
    ap.add_argument("--split", default="frames_split", choices=["frames_split", "time_split"],
                    help="which train/test split to use (frames_split = test repos never seen in training)")
    ap.add_argument("--cv", action="store_true", help="also run 5-fold cross-validation by repo")
    ap.add_argument("--repeats", type=int, default=1,
                    help="train every model with this many random seeds (and repeat the cross-validation with new folds)")
    ap.add_argument("--no-train-eval", action="store_true", help="evaluate only the test set (faster)")
    ap.add_argument("--boot", type=int, default=2000, help="resamples of repos for the evaluation's 95%% ranges")
    ap.add_argument("--fast", action="store_true", help="faster, slightly less accurate boosting settings")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--mlflow-experiment", default="riffle-at-open", help="MLflow experiment name (when installed)")
    ap.add_argument("--ensemble-members", nargs="+", default=["lgbm_history", "two_stage_history", "logreg"],
                    choices=[m for m in MODELS if m != "ensemble"], help="models the ensemble averages")
    args = ap.parse_args(argv)
    if "ensemble" in args.models:
        missing = [m for m in args.ensemble_members if m not in args.models]
        if missing:
            ap.error("the ensemble averages models trained in the same run; add to --models: " + " ".join(missing))
    return args


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
