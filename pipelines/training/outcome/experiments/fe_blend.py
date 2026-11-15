"""
fe_blend.py - blends of pass-3 models from their saved test predictions (fe_preds/p3_*).

    python fe_blend.py SPLIT MODEL [MODEL ...]      # e.g. repo lgb_default_base mlp_base seq_base

Blend = weighted average of the models' probabilities (weights on the simplex), then vector-scaling recalibration.
Weights and recalibration are cross-fitted: fitted on half of the test repos and scored on the other half, then
swapped, so the blend is never scored on the rows it was fitted on. Also tries a log-linear (geometric) blend.
Scored on the exact-diff view, as fe_run.py.
"""
import sys

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import softmax

import fe_run as R


def load(split, names, seed=0):
    zs = [np.load(f"fe_preds/p3_{n}_{split}_s{seed}.npz") for n in names]
    idx = zs[0]["idx"]
    for z in zs[1:]:
        assert np.array_equal(z["idx"], idx), "models scored different rows"
    return idx, [z["P"] for z in zs], zs[0]["base"]


def combine(Ps, w, geometric):
    if geometric:
        return softmax(sum(wi * np.log(np.clip(P, 1e-9, 1)) for wi, P in zip(w, Ps)), axis=1)
    return sum(wi * P for wi, P in zip(w, Ps))


def fit(Ps, y, wt, geometric):
    k = len(Ps)

    def nll(theta):
        w = softmax(np.r_[0.0, theta[:k - 1]])
        P = combine(Ps, w, geometric)
        Z = np.log(np.clip(P, 1e-9, 1)) / np.exp(theta[k - 1]) + np.r_[0.0, theta[k:]]
        Q = softmax(Z, axis=1)
        return -(wt * np.log(np.clip(Q[np.arange(len(y)), y], 1e-12, 1))).sum() / wt.sum()
    th = minimize(nll, np.zeros(k + 2), method="L-BFGS-B").x
    return th


def apply(Ps, th, geometric):
    k = len(Ps)
    w = softmax(np.r_[0.0, th[:k - 1]])
    Z = np.log(np.clip(combine(Ps, w, geometric), 1e-9, 1)) / np.exp(th[k - 1]) + np.r_[0.0, th[k:]]
    return softmax(Z, axis=1), w


def main():
    split, names = sys.argv[1], sys.argv[2:]
    d, _ = R.load()
    idx, Ps, base = load(split, names)
    y, repo = d["y"].to_numpy()[idx], d["repo"].to_numpy()[idx]
    ex = (d["open_snapshot"] == "exact").to_numpy()[idx]
    cnt = pd.DataFrame({"r": repo, "y": y, "x": ex}).groupby(["r", "y"])["x"].agg(["size", "sum"])
    wt_ex = (cnt["size"] / cnt["sum"]).reindex(pd.MultiIndex.from_arrays([repo, y])).to_numpy()
    wt = np.where(ex, wt_ex, 0.0)                          # exact rows, reweighted; other rows weight 0
    half = np.array([int(R.h(r, "blend") * 2) for r in repo])
    for n, P in zip(names, Ps):
        m = R.metrics(y[ex], P[ex], repo[ex], base[ex], wt[ex])
        print(f"{n:28s} skill {m['skill']:.4f}  log loss {m['log_loss']:.4f}  calib {m['calib']:.4f}")
    for geometric in (False, True):
        Q, ws = np.zeros_like(Ps[0]), []
        for k in (0, 1):
            fit_on, score_on = half != k, half == k
            th = fit([P[fit_on] for P in Ps], y[fit_on], wt[fit_on], geometric)
            Q[score_on], w = apply([P[score_on] for P in Ps], th, geometric)
            ws.append(w)
        m = R.metrics(y[ex], Q[ex], repo[ex], base[ex], wt[ex])
        print(f"{'geometric' if geometric else 'linear'} blend: skill {m['skill']:.4f}  log loss {m['log_loss']:.4f}  "
              f"calib {m['calib']:.4f}  weights " + " / ".join(" ".join(f"{v:.2f}" for v in w) for w in ws))


if __name__ == "__main__":
    main()
