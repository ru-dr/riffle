"""fe_calib.py - weighted calibration error on the exact-diff view, from saved predictions (fe_preds/).

    python fe_calib.py PREFIX      # e.g. p2_  (seed 0 files only)
Needed for passes 1 and 2, whose x_calib in fe_results.jsonl was unweighted."""
import glob, os, sys
import numpy as np, pandas as pd
import fe_run

d, _ = fe_run.load()
y, repo, exact = d["y"].to_numpy(), d["repo"].to_numpy(), (d["open_snapshot"] == "exact").to_numpy()
for f in sorted(glob.glob(f"fe_preds/{sys.argv[1] if len(sys.argv) > 1 else ''}*_s0.npz")):
    z = np.load(f)
    idx, P = z["idx"], z["P"]
    yy, rr, ex = y[idx], repo[idx], exact[idx]
    cnt = pd.DataFrame({"r": rr, "y": yy, "x": ex}).groupby(["r", "y"])["x"].agg(["size", "sum"])
    w = (cnt["size"] / cnt["sum"]).reindex(pd.MultiIndex.from_arrays([rr[ex], yy[ex]])).to_numpy()
    Y, Pe = np.eye(3)[yy[ex]], P[ex]
    e = []
    for k in range(3):
        b = np.minimum((Pe[:, k] * 10).astype(int), 9)
        g = pd.DataFrame({"b": b, "p": Pe[:, k] * w, "y": Y[:, k] * w}).groupby("b").sum()
        e.append(float((g["p"] - g["y"]).abs().sum() / w.sum()))
    print(f"{os.path.basename(f)[:-7]:45s} {np.mean(e):.4f}  " + " ".join(f"{v:.3f}" for v in e))
