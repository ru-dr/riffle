"""
fe_models.py - pass 3: model architectures on one frozen feature set. Same rows, splits, weights, calibration and
metrics as fe_run.py; only the learner changes.

    python fe_models.py [--only NAME ...] [--features base|base_new]

Learners: LightGBM (a few settings at learning rate 0.05), XGBoost (GPU), CatBoost (GPU), logistic regression, an MLP
(GPU). Each returns raw class scores; the same vector-scaling calibration is fitted on validation outside PRs.
Every run saves its test predictions to fe_preds/ (for blends) and its latency (ms per PR, batches of 256).
"""
from __future__ import annotations

import argparse
import json
import os
import time

import lightgbm as lgb
import numpy as np
import pandas as pd

import fe_features as F
import fe_run as R

RESULTS = os.path.join(R.HERE, "fe_models_results.jsonl")


# ---------------------------------------------------------------- preprocessing for the non-tree learners
class Dense:
    """Numbers -> signed log1p, standardised on the training rows, NaN -> 0 plus a missing flag;
    categories -> one-hot (training levels). Output float32."""

    def fit(self, X: pd.DataFrame):
        self.num = [c for c in X.columns if not isinstance(X[c].dtype, pd.CategoricalDtype)]
        self.cat = {c: list(X[c].cat.categories) for c in X.columns if c not in self.num}
        Z = self._log(X[self.num])
        self.mu, self.sd = Z.mean(), Z.std().replace(0, 1).fillna(1)
        self.miss = [c for c in self.num if X[c].isna().any()]
        return self

    @staticmethod
    def _log(Z):
        return np.sign(Z) * np.log1p(np.abs(Z))

    def transform(self, X: pd.DataFrame) -> np.ndarray:
        Z = ((self._log(X[self.num]) - self.mu) / self.sd).clip(-6, 6)
        parts = [Z.fillna(0).to_numpy(np.float32), X[self.miss].isna().to_numpy(np.float32)]
        for c, levels in self.cat.items():
            codes = pd.Categorical(X[c].astype(object), categories=levels).codes
            oh = np.zeros((len(X), len(levels)), np.float32)
            ok = codes >= 0
            oh[np.flatnonzero(ok), codes[ok]] = 1
            parts.append(oh)
        return np.hstack(parts)


# ---------------------------------------------------------------- learners: fit(...) -> predict_raw(X) callable
def lgb_learner(params):
    def fit(Xtr, ytr, wtr, Xva, yva, wva, seed):
        p = dict(R.PARAMS, **params, seed=seed)
        dtr = lgb.Dataset(Xtr, ytr, weight=wtr)
        m = lgb.train(p, dtr, 6000, valid_sets=[lgb.Dataset(Xva, yva, weight=wva, reference=dtr)],
                      callbacks=[lgb.early_stopping(150, verbose=False)])
        return lambda X: m.predict(X, raw_score=True)
    return fit


def xgb_learner(params):
    import xgboost as xgb

    def fit(Xtr, ytr, wtr, Xva, yva, wva, seed):
        p = dict(objective="multi:softprob", num_class=3, tree_method="hist", device="cuda", eta=0.05, max_depth=6,
                 min_child_weight=20, subsample=0.8, colsample_bytree=0.7, reg_lambda=5.0, max_cat_to_onehot=8,
                 eval_metric="mlogloss", seed=seed, **params)
        dtr = xgb.DMatrix(Xtr, ytr, weight=wtr, enable_categorical=True)
        dva = xgb.DMatrix(Xva, yva, weight=wva, enable_categorical=True)
        m = xgb.train(p, dtr, 6000, evals=[(dva, "va")], early_stopping_rounds=150, verbose_eval=False)
        it = (0, m.best_iteration + 1)
        return lambda X: np.log(np.clip(m.predict(xgb.DMatrix(X, enable_categorical=True), iteration_range=it),
                                        1e-9, 1))
    return fit


def cat_learner(params):
    from catboost import CatBoostClassifier, Pool

    def prep(X):
        X = X.copy()
        for c in X.columns:
            if isinstance(X[c].dtype, pd.CategoricalDtype):
                X[c] = X[c].astype(object).where(X[c].notna(), "__NA__").astype(str)
        return X

    def fit(Xtr, ytr, wtr, Xva, yva, wva, seed):
        cats = [c for c in Xtr.columns if isinstance(Xtr[c].dtype, pd.CategoricalDtype)]
        m = CatBoostClassifier(loss_function="MultiClass", iterations=6000, learning_rate=0.08, depth=6,
                               l2_leaf_reg=5, od_type="Iter", od_wait=150, random_seed=seed, task_type="GPU",
                               verbose=False, **params)
        m.fit(Pool(prep(Xtr), ytr, weight=wtr, cat_features=cats),
              eval_set=Pool(prep(Xva), yva, weight=wva, cat_features=cats), use_best_model=True)
        return lambda X: m.predict(Pool(prep(X), cat_features=cats), prediction_type="RawFormulaVal")
    return fit


def logreg_learner(params):
    from sklearn.linear_model import LogisticRegression

    def fit(Xtr, ytr, wtr, Xva, yva, wva, seed):
        enc = Dense().fit(Xtr)
        m = LogisticRegression(C=params.get("C", 0.1), max_iter=2000)
        m.fit(enc.transform(Xtr), ytr, sample_weight=wtr)
        return lambda X: m.decision_function(enc.transform(X))
    return fit


def mlp_learner(params):
    import torch
    import torch.nn as nn

    def fit(Xtr, ytr, wtr, Xva, yva, wva, seed):
        torch.manual_seed(seed)
        np.random.seed(seed)
        dev = "cuda" if torch.cuda.is_available() else "cpu"
        enc = Dense().fit(Xtr)
        A, B = (torch.tensor(enc.transform(x), device=dev) for x in (Xtr, Xva))
        ya, yb = (torch.tensor(np.asarray(v), device=dev) for v in (ytr, yva))
        wa, wb = (torch.tensor(np.asarray(v, np.float32), device=dev) for v in (wtr, wva))
        h, drop = params.get("hidden", (512, 256, 128)), params.get("dropout", 0.2)
        layers, k = [], A.shape[1]
        for n in h:
            layers += [nn.Linear(k, n), nn.LayerNorm(n), nn.SiLU(), nn.Dropout(drop)]
            k = n
        net = nn.Sequential(*layers, nn.Linear(k, 3)).to(dev)
        opt = torch.optim.AdamW(net.parameters(), lr=params.get("lr", 1e-3), weight_decay=params.get("wd", 1e-4))
        loss = nn.CrossEntropyLoss(reduction="none")
        best, best_state, bad, bs = np.inf, None, 0, 1024
        for epoch in range(params.get("epochs", 80)):
            net.train()
            perm = torch.randperm(len(A), device=dev)
            for i in range(0, len(A), bs):
                j = perm[i:i + bs]
                opt.zero_grad()
                (loss(net(A[j]), ya[j]) * wa[j]).sum().div(wa[j].sum()).backward()
                opt.step()
            net.eval()
            with torch.no_grad():
                v = float((loss(net(B), yb) * wb).sum() / wb.sum())
            if v < best - 1e-4:
                best, bad = v, 0
                best_state = {n_: t.detach().clone() for n_, t in net.state_dict().items()}
            else:
                bad += 1
                if bad >= 8:
                    break
        net.load_state_dict(best_state)
        net.eval().cpu()

        def predict(X):
            with torch.no_grad():
                return net(torch.tensor(enc.transform(X))).numpy()
        return predict
    return fit


LEARNERS = {
    "lgb_default": lgb_learner(dict(learning_rate=0.05)),
    "lgb_small_leaves": lgb_learner(dict(learning_rate=0.05, num_leaves=15, min_data_in_leaf=100, lambda_l2=10)),
    "lgb_big_leaves": lgb_learner(dict(learning_rate=0.05, num_leaves=63, min_data_in_leaf=50)),
    "lgb_strong_reg": lgb_learner(dict(learning_rate=0.05, num_leaves=31, min_data_in_leaf=200, lambda_l2=30,
                                       feature_fraction=0.5)),
    "xgb": xgb_learner({}),
    "catboost": cat_learner({}),
    "logreg": logreg_learner(dict(C=0.1)),
    "mlp": mlp_learner({}),
}


# ---------------------------------------------------------------- runs
def run(d, X, learner, split, rows, train_rows, wx, seeds, name):
    """Like fe_run.run_split, with a pluggable learner, and latency."""
    scored = rows & d["outside"].to_numpy()
    y, repo = d["y"].to_numpy(), d["repo"].to_numpy()
    outside = d["outside"].to_numpy()
    out = []
    for seed in seeds:
        P, base = np.full((len(d), 3), np.nan), np.full((len(d), 3), np.nan)
        if split == "pr":
            te = scored & (d["u_pr"].to_numpy() < 0.2)
            rest = train_rows & (d["u_pr"].to_numpy() >= 0.2)
            parts = [(rest & (d["u_val"].to_numpy() >= 0.15), rest & (d["u_val"].to_numpy() < 0.15), te)]
        else:
            fold, parts = d["fold"].to_numpy(), []
            for k in range(5):
                rest = train_rows & (fold != k)
                reps = sorted(set(repo[rest]), key=lambda r: R.h(r, f"val{k}"))
                va = rest & np.isin(repo, reps[:max(2, round(0.15 * len(reps)))])
                parts.append((rest & ~va, va, scored & (fold == k)))
        lat = []
        for tr, va, te in parts:
            w = R.repo_weights(repo[tr]) * wx[tr]
            predict = learner(X[tr], y[tr], w, X[va], y[va], wx[va], seed)
            cv = va & outside
            cal = R.Calibrator().fit(predict(X[cv]), y[cv], R.repo_weights(repo[cv]) * wx[cv])
            Xte = X[te]
            t0 = time.perf_counter()
            Z = np.vstack([predict(Xte.iloc[i:i + 256]) for i in range(0, len(Xte), 256)])
            lat.append((time.perf_counter() - t0) * 1000 / max(len(Xte), 1))
            P[te] = cal(Z)
            base[te] = np.bincount(y[tr & scored], minlength=3) / (tr & scored).sum()
        mask = scored if split == "repo" else parts[0][2]
        m_all = R.metrics(y[mask], P[mask], repo[mask], base[mask])
        ex = mask & (d["open_snapshot"] == "exact").to_numpy()
        cnt = pd.DataFrame({"r": repo[mask], "y": y[mask], "x": ex[mask]}).groupby(["r", "y"])["x"].agg(["size", "sum"])
        wt = (cnt["size"] / cnt["sum"]).reindex(pd.MultiIndex.from_arrays([repo[ex], y[ex]])).to_numpy()
        m_ex = R.metrics(y[ex], P[ex], repo[ex], base[ex], wt)
        out.append({**m_all, **{"x_" + k: v for k, v in m_ex.items()}, "latency_ms": float(np.mean(lat))})
        np.savez_compressed(os.path.join(R.HERE, "fe_preds", f"{name}_{split}_s{seed}.npz"),
                            idx=np.flatnonzero(mask), P=P[mask], base=base[mask])
    m = {k: float(np.mean([o[k] for o in out])) for k in out[0]}
    if len(seeds) > 1:
        m["x_skill_seed_sd"] = float(np.std([o["x_skill"] for o in out]))
    return m


def feature_set(g, which):
    allowed = [c for c in g["allowed"] if c != "open_snapshot"]
    clean = [c for c in allowed if c not in g["leaks"]]
    cons = [c for c in clean if c not in g["llm_detail"] and c not in g["redundant"]] + g["consolidated"]
    base = cons + g["author_history"] + g["repo_history"]
    if which == "base_new":
        base = base + g["author_recent"] + g["repo_ft90"] + g["rel_size"]
    return base


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--features", default="base")
    ap.add_argument("--seeds", type=int, nargs="+", default=[0])
    ap.add_argument("--splits", nargs="+", default=["pr", "repo"])
    a = ap.parse_args()
    d, allowed = R.load()
    g = F.feature_groups(d, allowed)
    cols = feature_set(g, a.features)
    X = R.encode(d, cols)
    rows = d["mature"].to_numpy() & (d["open_snapshot"] != "final").to_numpy()
    # the pass-2 default: outside PRs, exact diffs only, reweighted to each repo's class mix
    train_rows = rows & d["outside"].to_numpy()
    ex = (d["open_snapshot"] == "exact").to_numpy()
    cnt = pd.DataFrame({"r": d["repo"], "y": d["y"], "x": ex})[train_rows].groupby(["r", "y"])["x"].agg(["size", "sum"])
    wx = np.nan_to_num((cnt["size"] / cnt["sum"].clip(lower=1)).reindex(
        pd.MultiIndex.from_arrays([d["repo"], d["y"]])).to_numpy(), nan=1.0)
    train_rows &= ex
    done = set()
    if os.path.exists(RESULTS):
        done = {(j["model"], j["split"], j["features"], j["seeds"]) for j in map(json.loads, open(RESULTS))}
    for name, learner in LEARNERS.items():
        if a.only and name not in a.only:
            continue
        for split in a.splits:
            key = (name, split, a.features, len(a.seeds))
            if key in done:
                continue
            t0 = time.time()
            m = run(d, X, learner, split, rows, train_rows, wx, a.seeds, f"p3_{name}_{a.features}")
            row = dict(model=name, split=split, features=a.features, seeds=len(a.seeds), n_features=len(cols),
                       seconds=round(time.time() - t0), **m)
            print(json.dumps(row), flush=True)
            with open(RESULTS, "a") as fh:
                fh.write(json.dumps(row) + "\n")


if __name__ == "__main__":
    os.makedirs(os.path.join(R.HERE, "fe_preds"), exist_ok=True)
    main()
