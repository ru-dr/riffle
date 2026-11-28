"""Running the outcome model (setup G, ADR 0023) exactly as training does.

The model-running code below is copied from the training notebook
(pipelines/training/outcome/pipeline.ipynb, source in experiments/notebook_build/pipeline_src.py:
_flag, Encoder, Dense, apply_vector, apply_blend, member_scores, predict_bundle,
prediction_columns, load_bundle). The notebook's file is a template and cannot be imported, so
the code lives here; change it only together with the notebook. The engineered features come
from pipelines/training/outcome_features.py (build_features), shared with training.

A saved model folder (one variant: seen_repos or new_repos) holds meta.json (the feature list in
order, category levels, calibration, blend) and, per seed, LightGBM, XGBoost and logistic
regression files. No pickles.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

import lightgbm as lgb
import numpy as np
import pandas as pd
import xgboost as xgb
from scipy.special import expit, softmax
from sklearn.linear_model import LogisticRegression

import outcome_features as OF

REASONS = ("abandoned", "duplicate", "unwanted", "ci_technical", "unknown")
CLOSE_NAME = {"abandoned": "abandoned", "duplicate": "duplicate", "unwanted": "unwanted",
              "ci_technical": "ci_technical", "unknown": "other"}
BINARY_TASKS = ("bad", "bad_miner", "revert", "hotfix", "szz_bug")
MEMBERS = ("lgb", "xgb", "logreg")
CATEGORICAL = ("migration_reversibility", "ghsa_max_severity_introduced", "api_contract_change",
               "refactor_vs_behavior", "change_intent", "tests_added_for_behavior_change")


def _flag(s: pd.Series) -> np.ndarray:
    """True / False / unknown (NaN) from bools, 0/1 or strings."""
    m = s.map(lambda v: 1.0 if v is True or v == 1 or str(v).lower() == "true" else
              0.0 if v is False or v == 0 or str(v).lower() == "false" else np.nan)
    return m.to_numpy(float)


class Encoder:
    """Raw columns -> the model matrix: numbers as floats, yes/no as 1/0, categories with the training levels."""

    def __init__(self, features, levels=None):
        self.features = list(features)
        self.levels = levels or {}

    def transform(self, df):
        out = {}
        for c in self.features:
            s = df[c] if c in df else pd.Series(np.nan, index=df.index)
            if self.levels.get(c):
                out[c] = pd.Categorical(s.where(s.isna(), s.astype(str)), categories=self.levels[c])
            elif c in CATEGORICAL:      # no value seen in training: unknown everywhere (XGBoost needs a level)
                out[c] = np.full(len(df), np.nan)
            elif s.dtype == object or pd.api.types.is_bool_dtype(s):
                out[c] = pd.Series(_flag(s), index=df.index) if s.dtype == object else s.astype(float)
            else:
                out[c] = pd.to_numeric(s, errors="coerce").astype(float)
        return pd.DataFrame(out, index=df.index)


class Dense:
    """For logistic regression: signed log1p, standardised, missing flags, one-hot categories. Saved as JSON."""

    @staticmethod
    def _log(Z):
        return np.sign(Z) * np.log1p(np.abs(Z))

    def transform(self, X):
        Z = ((self._log(X[self.num]) - self.mu) / self.sd).clip(-6, 6)
        parts = [Z.fillna(0).to_numpy(np.float32), X[self.miss].isna().to_numpy(np.float32)]
        for c, levels in self.cat.items():
            codes = pd.Categorical(X[c].astype(object), categories=levels).codes
            oh = np.zeros((len(X), len(levels)), np.float32)
            oh[np.flatnonzero(codes >= 0), codes[codes >= 0]] = 1
            parts.append(oh)
        return np.hstack(parts)

    @classmethod
    def from_dict(cls, o):
        e = cls()
        e.num, e.cat, e.miss = o["num"], o["cat"], o["miss"]
        e.mu, e.sd = pd.Series(o["mu"], index=e.num), pd.Series(o["sd"], index=e.num)
        return e


def apply_vector(Z, t):
    return softmax(Z / np.exp(t[0]) + np.r_[0.0, t[1:]], axis=1)


def apply_blend(Ps, th):
    k = len(Ps)
    wm = softmax(np.r_[0.0, th[:k - 1]])
    Z = np.log(np.clip(softmax(sum(a * np.log(np.clip(P, 1e-9, 1)) for a, P in zip(wm, Ps)), axis=1), 1e-9, 1))
    return softmax(Z / np.exp(th[k - 1]) + np.r_[0.0, th[k:]], axis=1)


def member_scores(name, m, X):
    """Raw class scores of one outcome member."""
    if name == "lgb":
        return m.predict(X, raw_score=True)
    if name == "xgb":
        booster, n = m
        return np.log(np.clip(booster.predict(xgb.DMatrix(X, enable_categorical=True), iteration_range=(0, n)),
                              1e-9, 1))
    enc, lr = m
    return lr.decision_function(enc.transform(X))


def predict_bundle(bundle, X, seeds=None):
    """P (n, 3) outcome, R (n, 5) reasons, B {task: (n,)}; averaged over the given seeds (default: all)."""
    seeds = list(bundle["seeds"]) if seeds is None else seeds
    P, R, B = 0, 0, {t: 0 for t in BINARY_TASKS}
    for s in seeds:
        o = bundle["seeds"][s]
        Ps = [apply_vector(member_scores(n, o["outcome"]["models"][n], X), o["outcome"]["cal"][n]) for n in MEMBERS]
        P = P + apply_blend(Ps, o["outcome"]["blend"])
        rm, rt, rlam, rbase = o["reason"]
        R = R + rlam * apply_vector(rm.predict(X, raw_score=True), rt) + (1 - rlam) * np.asarray(rbase)
        for task in BINARY_TASKS:
            b = o["binary"][task]
            B[task] = B[task] + (np.full(len(X), b[1]) if b[0] == "const" else
                                 b[2] * expit(b[1][0] * b[0].predict(X, raw_score=True) + b[1][1]) + (1 - b[2]) * b[3])
    k = len(seeds)
    return P / k, R / k, {t: v / k for t, v in B.items()}


def prediction_columns(P, R, B):
    P = np.clip(P, 1e-6, 1)
    P = P / P.sum(1, keepdims=True)
    R = np.clip(R, 1e-6, 1)
    R = R / R.sum(1, keepdims=True)
    o = {"p_clean": P[:, 0], "p_rework": P[:, 1], "p_nowhere": P[:, 2]}
    for i, r in enumerate(REASONS):
        o[f"p_reason_{r}"] = R[:, i]
    for i, r in enumerate(REASONS):
        o[f"p_close_reason_{CLOSE_NAME[r]}"] = R[:, i]
    o["p_label_merged"] = P[:, 0] + P[:, 1]
    o["p_label_rework"] = P[:, 1] / (P[:, 0] + P[:, 1])
    for i, r in enumerate(REASONS[:4]):
        o[f"p_label_{r}"] = R[:, i]
    for task in ("bad", "revert", "szz_bug", "hotfix", "bad_miner"):
        o[f"p_label_{task}"] = np.broadcast_to(B[task], (len(P),)).astype(float)
    return pd.DataFrame(o)


def load_bundle(path):
    meta = json.load(open(os.path.join(path, "meta.json")))
    b = {"encoder": Encoder(meta["features"], meta["categorical_levels"]), "heuristic": meta["heuristic"], "seeds": {},
         "meta": meta}
    for s, m in meta["seeds"].items():
        s = int(s)
        bx = xgb.Booster()
        bx.load_model(os.path.join(path, f"outcome_xgb_s{s}.ubj"))
        lj = json.load(open(os.path.join(path, f"outcome_logreg_s{s}.json")))
        lr = LogisticRegression()
        lr.coef_, lr.intercept_, lr.classes_ = np.array(lj["coef"]), np.array(lj["intercept"]), np.arange(3)
        models = {"lgb": lgb.Booster(model_file=os.path.join(path, f"outcome_lgb_s{s}.txt")),
                  "xgb": (bx, m["xgb_rounds"]), "logreg": (Dense.from_dict(lj["dense"]), lr)}
        b["seeds"][s] = {
            "outcome": {"models": models, "cal": {n: np.array(m["calibration"][n]) for n in MEMBERS},
                        "blend": np.array(m["blend"])},
            "reason": (lgb.Booster(model_file=os.path.join(path, f"reason_lgb_s{s}.txt")),
                       np.array(m["reason_calibration"]), m["reason_shrink"], np.array(m["reason_base_rate"])),
            "binary": {t: ((lgb.Booster(model_file=os.path.join(path, f"label_{t}_lgb_s{s}.txt")),
                            np.array(m["label_platt"][t][:2]), m["label_platt"][t][2], m["label_platt"][t][3])
                           if t in m["label_platt"] else
                           ("const", m["label_base_rate"][t]))
                       for t in BINARY_TASKS}}
    return b


# ---- the scorer's use of the above ----

@dataclass(frozen=True)
class Prediction:
    p_lands_clean: float
    p_lands_after_rework: float
    p_goes_nowhere: float
    reasons: dict          # abandoned, duplicate, unwanted, ci_technical, unknown
    labels: dict           # bad, revert, hotfix, szz_bug
    features: dict         # the model's input row, name -> value, for the score log


class Variant:
    """One loaded model folder (seen_repos or new_repos)."""

    def __init__(self, path: str):
        self.bundle = load_bundle(path)
        self.features = list(self.bundle["meta"]["features"])
        missing = sorted(set(self.features) - set(OF.FEATURES))
        if missing:
            raise ValueError(f"the model reads features outcome_features.py does not build: {missing[:5]}")


def is_outside_contributor(row: dict) -> bool:
    """The model was trained and is evaluated on outside contributors only: build_features' `outside` rule
    (not a bot, not a confirmed maintainer; unknown counts as neither)."""
    hit = lambda c: np.nan_to_num(OF.flag(pd.Series([row.get(c)], dtype=object)))[0] == 1
    return not hit("author_is_bot") and not hit("author_confirmed_maintainer")


def predict(variant: Variant, history: pd.DataFrame, row: dict) -> Prediction:
    """Score one PR the way training scores it: the repo's earlier rows plus this row through
    build_features (history only from PRs that closed before this one opened), then the model."""
    if len(history):
        history = history[history["pr_number"] != row["pr_number"]]      # an earlier row of this PR (reopened)
    table = pd.concat([history, pd.DataFrame([row])], ignore_index=True) if len(history) else pd.DataFrame([row])
    d = OF.build_features(table)
    X = variant.bundle["encoder"].transform(d.iloc[[len(d) - 1]])
    cols = prediction_columns(*predict_bundle(variant.bundle, X)).iloc[0]
    features = {c: _plain(X[c].iloc[0]) for c in variant.features}
    return Prediction(
        p_lands_clean=float(cols["p_clean"]), p_lands_after_rework=float(cols["p_rework"]),
        p_goes_nowhere=float(cols["p_nowhere"]),
        reasons={r: float(cols[f"p_reason_{r}"]) for r in REASONS},
        labels={t: float(cols[f"p_label_{t}"]) for t in ("bad", "revert", "hotfix", "szz_bug")},
        features=features)


def _plain(v):
    """A JSON-safe value for the score log: NaN becomes null, numpy numbers plain numbers."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    if isinstance(v, (np.floating, np.integer)):
        f = float(v)
        return None if np.isnan(f) else f
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    return v if isinstance(v, (int, float, str)) else str(v)
