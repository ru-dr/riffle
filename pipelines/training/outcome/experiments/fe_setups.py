"""
fe_setups.py - pass 4: quality of each candidate setup over 3 seeds, both splits, from saved predictions.

    python fe_setups.py            # writes fe_setups.json and prints the table

Each setup is a geometric blend (fe_blend.py: weights and recalibration cross-fitted on halves of the test repos;
a single model goes through the same recalibration so it compares fairly). Reported on the exact view: mean and sd
of skill over seeds 0-2, log loss, calibration error (overall and per class), and the weakest repo's skill.
"""
import json

import numpy as np
import pandas as pd

import fe_blend as B
import fe_run as R

SETUPS = {
    "A LightGBM": ["lgb_default"],
    "A' LightGBM, strong regularisation": ["lgb_strong_reg"],
    "B LightGBM + XGBoost": ["lgb_default", "xgb"],
    "C LightGBM + sequence": ["lgb_default", "seq"],
    "D LightGBM + logistic regression": ["lgb_default", "logreg"],
    "E LightGBM + sequence + MLP": ["lgb_default", "seq", "mlp"],
    "F LightGBM + XGBoost + sequence + MLP": ["lgb_default", "xgb", "seq", "mlp"],
    "G LightGBM + XGBoost + logistic regression": ["lgb_default", "xgb", "logreg"],
    "H LightGBM + XGBoost + sequence": ["lgb_default", "xgb", "seq"],
}


def per_class_calib(y, P, w):
    Y, out = np.eye(3)[y], []
    for k in range(3):
        b = np.minimum((P[:, k] * 10).astype(int), 9)
        g = pd.DataFrame({"b": b, "p": P[:, k] * w, "y": Y[:, k] * w}).groupby("b").sum()
        out.append(float((g["p"] - g["y"]).abs().sum() / w.sum()))
    return out


def score(d, split, members, seed):
    idx, Ps, base = B.load(split, [f"{m}_base" for m in members], seed)
    y, repo = d["y"].to_numpy()[idx], d["repo"].to_numpy()[idx]
    ex = (d["open_snapshot"] == "exact").to_numpy()[idx]
    cnt = pd.DataFrame({"r": repo, "y": y, "x": ex}).groupby(["r", "y"])["x"].agg(["size", "sum"])
    wt = np.where(ex, (cnt["size"] / cnt["sum"]).reindex(pd.MultiIndex.from_arrays([repo, y])).to_numpy(), 0.0)
    half = np.array([int(R.h(r, "blend") * 2) for r in repo])
    Q, ws = np.zeros_like(Ps[0]), []
    for k in (0, 1):
        f, s = half != k, half == k
        th = B.fit([P[f] for P in Ps], y[f], wt[f], True)
        Q[s], w = B.apply([P[s] for P in Ps], th, True)
        ws.append(w.tolist())
    m = R.metrics(y[ex], Q[ex], repo[ex], base[ex], wt[ex])
    Y = np.eye(3)[y[ex]]
    rb = pd.DataFrame({"r": repo[ex], "b": ((Q[ex] - Y) ** 2).sum(1) * wt[ex], "rb": ((base[ex] - Y) ** 2).sum(1) * wt[ex],
                       "w": wt[ex]}).groupby("r").sum()
    repo_skill = 1 - (rb["b"] / rb["w"]) / (rb["rb"] / rb["w"])
    return dict(skill=m["skill"], log_loss=m["log_loss"], calib=m["calib"],
                calib_per_class=per_class_calib(y[ex], Q[ex], wt[ex]),
                worst_repo=repo_skill.idxmin(), worst_repo_skill=float(repo_skill.min()),
                repos_below_zero=int((repo_skill < 0).sum()), weights=ws)


def main():
    d, _ = R.load()
    out = {}
    for name, members in SETUPS.items():
        for split in ("pr", "repo"):
            rs = [score(d, split, members, s) for s in (0, 1, 2)]
            row = dict(skill=float(np.mean([r["skill"] for r in rs])), skill_sd=float(np.std([r["skill"] for r in rs])),
                       log_loss=float(np.mean([r["log_loss"] for r in rs])),
                       calib=float(np.mean([r["calib"] for r in rs])),
                       calib_worst_class=float(np.mean([max(r["calib_per_class"]) for r in rs])),
                       worst_repo=rs[0]["worst_repo"],
                       worst_repo_skill=float(np.mean([r["worst_repo_skill"] for r in rs])),
                       repos_below_zero=float(np.mean([r["repos_below_zero"] for r in rs])),
                       weights_seed0=rs[0]["weights"])
            out[f"{name} | {split}"] = row
            print(f"{name:42s} {split:4s} skill {row['skill']:.4f} ±{row['skill_sd']:.4f}  ll {row['log_loss']:.4f}  "
                  f"calib {row['calib']:.4f} (worst class {row['calib_worst_class']:.4f})  worst repo "
                  f"{row['worst_repo']} {row['worst_repo_skill']:.3f}  below0 {row['repos_below_zero']:.1f}", flush=True)
    json.dump(out, open("fe_setups.json", "w"), indent=1)


if __name__ == "__main__":
    main()
