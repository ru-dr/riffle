"""fe_fingerprint.py - can a model tell a recovered (or approx) at-open diff from an exact one, among PRs with the same
outcome? Grouped 5-fold CV by repo; AUROC near 0.5 = no fingerprint."""
import json, sys
import numpy as np, pandas as pd, lightgbm as lgb
from sklearn.metrics import roc_auc_score
import fe_run, fe_features as F, frames

d, allowed = fe_run.load()
diff = [c for c in frames.diff_derived_columns() if c in d.columns] + ["base_behind_by"]
g = F.feature_groups(d, allowed)
clean = [c for c in g["allowed"] if c not in g["leaks"]]
rows = (d["open_snapshot"] != "final") & d["mature"]
out = {}
for cls in ("lands_after_rework", "goes_nowhere"):
    r = d[rows & (d["outcome_3class"] == cls)].reset_index(drop=True)
    target = (r["open_snapshot"] != "exact").astype(int).to_numpy()
    for name, cols, blanks in (("diff values", diff, False), ("diff blanks only", diff, True), ("all clean inputs", clean, False)):
        X = fe_run.encode(r, cols)
        if blanks:
            X = X.isna().astype(float)
        P = np.zeros(len(r))
        for k in range(5):
            te = (r["fold"] == k).to_numpy()
            m = lgb.train(dict(objective="binary", learning_rate=0.05, num_leaves=31, min_data_in_leaf=100,
                               feature_fraction=0.7, num_threads=4, verbose=-1), lgb.Dataset(X[~te], target[~te]), 300)
            P[te] = m.predict(X[te])
        a = float(roc_auc_score(target, P))
        per_repo = r.assign(p=P, t=target).groupby("repo").apply(
            lambda x: roc_auc_score(x.t, x.p) if x.t.nunique() == 2 else np.nan, include_groups=False)
        out[f"{cls} | {name}"] = dict(auroc=round(a, 3), auroc_within_repo=round(float(per_repo.mean()), 3),
                                      share_not_exact=round(float(target.mean()), 3), n=len(r))
        print(cls, "|", name, out[f"{cls} | {name}"], flush=True)
# blank-rate gaps, recovered vs exact, among rework PRs
r = d[rows & (d["outcome_3class"] == "lands_after_rework")]
gap = (r[r.open_snapshot != "exact"][diff].isna().mean() - r[r.open_snapshot == "exact"][diff].isna().mean()).abs()
print("largest blank-rate gaps (rework PRs, not-exact minus exact):")
print(gap.sort_values(ascending=False).head(8).round(3).to_string())
json.dump(out, open("fe_fingerprint.json", "w"), indent=1)
