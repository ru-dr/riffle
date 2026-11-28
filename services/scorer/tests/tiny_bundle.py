"""A tiny model folder in the exact format the training notebook saves (save_bundle in
pipelines/training/outcome/experiments/notebook_build/pipeline_src.py), trained in a second on random rows, so the
scorer's loading and scoring run in tests without the real model (89 MB, not in git)."""
from __future__ import annotations

import json
import os

import lightgbm as lgb
import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.linear_model import LogisticRegression

import outcome_features as OF
from scorer import predict

LEVELS = {"change_intent": ["bugfix", "feature", "refactor"]}


def _dense_fit(X):
    """Dense.fit from the notebook (the scorer only needs from_dict and transform)."""
    num = [c for c in X.columns if not isinstance(X[c].dtype, pd.CategoricalDtype)]
    cat = {c: list(X[c].cat.categories) for c in X.columns if c not in num}
    Z = predict.Dense._log(X[num])
    mu, sd = Z.mean().fillna(0), Z.std().replace(0, 1).fillna(1)
    miss = [c for c in num if X[c].isna().any()]
    return {"num": num, "cat": cat, "mu": mu.tolist(), "sd": sd.tolist(), "miss": miss}


def make(path: str, n: int = 400, seed: int = 0) -> str:
    os.makedirs(path, exist_ok=True)
    rng = np.random.default_rng(seed)
    features = list(OF.FEATURES)
    raw = pd.DataFrame({c: rng.gamma(1.0, 3.0, n) for c in features if c not in predict.CATEGORICAL})
    raw["change_intent"] = rng.choice(LEVELS["change_intent"], n)
    raw.loc[rng.random(n) < 0.3, "EXP"] = np.nan
    X = predict.Encoder(features, LEVELS).transform(raw)
    y = np.where(raw["LA"] > 4, 0, np.where(raw["NF"] > 3, 1, 2))
    p = {"verbose": -1, "num_leaves": 4, "min_data_in_leaf": 5, "seed": seed}
    lgb.train({**p, "objective": "multiclass", "num_class": 3}, lgb.Dataset(X, y), 8) \
        .save_model(os.path.join(path, "outcome_lgb_s0.txt"))
    xgb.train({"objective": "multi:softprob", "num_class": 3, "max_depth": 2, "tree_method": "hist", "seed": seed},
              xgb.DMatrix(X, y, enable_categorical=True), 8).save_model(os.path.join(path, "outcome_xgb_s0.ubj"))
    dense = _dense_fit(X)
    lr = LogisticRegression(max_iter=300).fit(predict.Dense.from_dict(dense).transform(X), y)
    json.dump({"dense": dense, "coef": lr.coef_.tolist(), "intercept": lr.intercept_.tolist()},
              open(os.path.join(path, "outcome_logreg_s0.json"), "w"))
    lgb.train({**p, "objective": "multiclass", "num_class": 5}, lgb.Dataset(X, rng.integers(0, 5, n)), 5) \
        .save_model(os.path.join(path, "reason_lgb_s0.txt"))
    lgb.train({**p, "objective": "binary"}, lgb.Dataset(X, (raw["LD"] > 5).astype(int)), 5) \
        .save_model(os.path.join(path, "label_bad_lgb_s0.txt"))
    meta = {"features": features, "categorical_levels": LEVELS, "heuristic": None,
            "reasons": list(predict.REASONS), "binary_tasks": list(predict.BINARY_TASKS),
            "members": list(predict.MEMBERS), "name": "new_repos", "built": "test",
            "seeds": {"0": {"xgb_rounds": 8,
                            "calibration": {m: [0.0, 0.1, -0.1] for m in predict.MEMBERS},
                            "blend": [0.0, 0.0, 0.0, 0.0, 0.0],
                            "reason_calibration": [0.0, 0.0, 0.0, 0.0, 0.0], "reason_shrink": 0.5,
                            "reason_base_rate": [0.4, 0.1, 0.2, 0.1, 0.2],
                            "label_platt": {"bad": [1.0, 0.0, 0.8, 0.1]},
                            "label_base_rate": {"bad_miner": 0.1, "revert": 0.02, "hotfix": 0.03, "szz_bug": 0.05}}}}
    json.dump(meta, open(os.path.join(path, "meta.json"), "w"))
    return path
