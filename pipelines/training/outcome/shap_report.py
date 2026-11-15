"""
shap_report.py - SHAP feature attributions for setup G's LightGBM member (guideline 5), from the saved models.

    python shap_report.py --data riffle_mined_v0.1.zip            # or a dataset parquet
    python shap_report.py --data ... --bundle seen_repos --rows 8000

What it explains: the LightGBM member of the blend, the largest share of most seeds' blend weights. Its raw class
scores are additive, so LightGBM's built-in TreeSHAP (predict(..., pred_contrib=True)) gives exact SHAP values
without the shap library. The XGBoost and logistic-regression members and the calibration on top are not included;
the blend as a whole is not additive.

Rows: a random sample (--rows, seed 0) of the PRs the model is scored on (outside contributors, mature, known
outcome, diff not 'final'), features built by outcome_features.build_features() and encoded with the bundle's own
categorical levels from meta.json, as the notebook's Encoder does. Each seed's booster is explained and the SHAP
values are averaged over seeds.

Writes to eval/by_repo/shap/ (eval/by_pr/shap/ for --bundle seen_repos):
  shap_importance.csv      every feature: mean |SHAP| per outcome class and in total, and its share of the total
  shap_groups.csv          the same, summed by feature group (author history, repo history, size, LLM, ...)
  shap_top_features.png    the 20 largest features by mean |SHAP|, split by class
  shap_nowhere_beeswarm.png  the 15 largest for 'goes nowhere': each dot a PR, colour = the feature's value
Needs lightgbm, numpy, pandas, matplotlib and pyarrow (for parquet).
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import zipfile

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))          # pipelines/training, for outcome_features.py
import outcome_features as OF  # noqa: E402

CLASSES = ("lands clean", "lands after rework", "goes nowhere")
GROUPS = (("ah_", "author history in this repo"), ("rh", "repo history"),
          (("semantic_risk", "c_llm", "llm_", "change_intent", "refactor_vs", "tests_added", "api_contract",
            "migration_rev"), "LLM reading of the diff"),
          (("LA", "LD", "LT", "NF", "ND", "NS", "c_churn", "c_delete", "c_files"), "size and spread of the change"),
          (("EXP", "SEXP", "REXP", "c_exp", "author_"), "author experience and account"))


def read_dataset(path):
    if path.endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            name = next(n for n in z.namelist() if n.endswith("dataset_merged.parquet"))
            return pd.read_parquet(io.BytesIO(z.read(name)))
    return pd.read_parquet(path)


def encode(d, features, levels):
    """The notebook's Encoder.transform, from meta.json's feature list and categorical levels."""
    out = {}
    for c in features:
        s = d[c] if c in d else pd.Series(np.nan, index=d.index)
        if c in levels:
            out[c] = (pd.Categorical(s.where(s.isna(), s.astype(str)), categories=levels[c]) if levels[c]
                      else np.full(len(d), np.nan))
        elif s.dtype == object:
            out[c] = OF.flag(s)
        elif pd.api.types.is_bool_dtype(s):
            out[c] = s.astype(float)
        else:
            out[c] = pd.to_numeric(s, errors="coerce").astype(float)
    return pd.DataFrame(out, index=d.index)


def group_of(feature):
    for prefixes, name in GROUPS:
        if feature.startswith(prefixes if isinstance(prefixes, tuple) else (prefixes,)):
            return name
    return "other"


def shap_values(boosters, X, n_classes=3):
    """SHAP values (rows x features x classes), averaged over boosters; the bias column is dropped."""
    k = X.shape[1]
    total = np.zeros((len(X), k, n_classes))
    for b in boosters:
        c = np.asarray(b.predict(X, pred_contrib=True)).reshape(len(X), n_classes, k + 1)
        total += np.transpose(c[:, :, :k], (0, 2, 1))
    return total / len(boosters)


def importance_tables(S, features):
    mean_abs = np.abs(S).mean(axis=0)                                    # features x classes
    imp = pd.DataFrame(mean_abs, columns=[f"mean_abs_{c.replace(' ', '_')}" for c in CLASSES])
    imp.insert(0, "feature", features)
    imp["mean_abs_total"] = mean_abs.sum(axis=1)
    imp["share"] = imp["mean_abs_total"] / imp["mean_abs_total"].sum()
    imp["group"] = [group_of(f) for f in features]
    imp = imp.sort_values("mean_abs_total", ascending=False).reset_index(drop=True)
    groups = imp.groupby("group", as_index=False)[[c for c in imp.columns if c.startswith("mean_abs") or
                                                   c == "share"]].sum().sort_values("share", ascending=False)
    return imp, groups


def charts(S, X, imp, out_dir, title):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    ink, muted, grid = "#0b0b0b", "#52514e", "#e4e3df"
    colours = ("#2a78d6", "#e0a030", "#c0392b")
    top = imp.head(20).iloc[::-1]
    fig, ax = plt.subplots(figsize=(9, 7.5), facecolor="white")
    left = np.zeros(len(top))
    for j, c in enumerate(CLASSES):
        v = top[f"mean_abs_{c.replace(' ', '_')}"].to_numpy()
        ax.barh(range(len(top)), v, left=left, color=colours[j], height=0.65, label=c)
        left += v
    ax.set_yticks(range(len(top)), top["feature"], fontsize=8, color=ink)
    ax.set_xlabel("mean |SHAP value| (raw score of the LightGBM member)", fontsize=9, color=muted)
    ax.set_title(f"Top 20 features by SHAP, {title}", fontsize=11, color=ink, loc="left")
    ax.legend(frameon=False, fontsize=8, loc="lower right")
    ax.grid(axis="x", color=grid, lw=0.6)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "shap_top_features.png"), dpi=160)
    plt.close(fig)

    order = imp.sort_values("mean_abs_goes_nowhere", ascending=False)["feature"].head(15).tolist()[::-1]
    cols = list(X.columns)
    rng = np.random.default_rng(0)
    fig, ax = plt.subplots(figsize=(9, 6.5), facecolor="white")
    for i, f in enumerate(order):
        j = cols.index(f)
        sv = S[:, j, 2]
        x = X[f]
        val = (x.cat.codes.replace(-1, np.nan).to_numpy(float) if hasattr(x, "cat")
               else pd.to_numeric(x, errors="coerce").to_numpy(float))
        pct = pd.Series(val).rank(pct=True).to_numpy()
        ok = np.isfinite(pct)
        jitter = rng.uniform(-0.3, 0.3, len(sv))
        ax.scatter(sv[~ok], i + jitter[~ok], s=3, color="#b8b8b8", alpha=0.5, lw=0)
        sc = ax.scatter(sv[ok], i + jitter[ok], s=3, c=pct[ok], cmap="coolwarm", vmin=0, vmax=1, alpha=0.6, lw=0)
    ax.axvline(0, color=muted, lw=0.8)
    ax.set_yticks(range(len(order)), order, fontsize=8, color=ink)
    ax.set_xlabel("SHAP value for 'goes nowhere' (right = more likely to go nowhere)", fontsize=9, color=muted)
    ax.set_title(f"How the top features move 'goes nowhere', {title}", fontsize=11, color=ink, loc="left")
    cb = fig.colorbar(sc, ax=ax, fraction=0.03, pad=0.02)
    cb.set_label("feature value (low to high; grey = blank)", fontsize=8, color=muted)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "shap_nowhere_beeswarm.png"), dpi=160)
    plt.close(fig)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], epilog=__doc__.split("\n\n", 1)[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=os.environ.get("RIFFLE_DATA", "riffle_mined_v0.1.zip"))
    ap.add_argument("--bundle", default="new_repos", choices=["new_repos", "seen_repos"])
    ap.add_argument("--models", default=os.path.join(HERE, "models"))
    ap.add_argument("--rows", type=int, default=5000, help="PRs to explain (a random sample)")
    args = ap.parse_args(argv)
    import lightgbm as lgb

    path = os.path.join(args.models, args.bundle)
    meta = json.load(open(os.path.join(path, "meta.json"), encoding="utf-8"))
    boosters = [lgb.Booster(model_file=os.path.join(path, f"outcome_lgb_s{s}.txt")) for s in sorted(meta["seeds"])]
    d = OF.build_features(read_dataset(args.data))
    rows = np.flatnonzero(OF.training_rows(d).scored)
    pick = np.random.default_rng(0).choice(rows, size=min(args.rows, len(rows)), replace=False)
    X = encode(d.iloc[np.sort(pick)].reset_index(drop=True), meta["features"], meta.get("categorical_levels", {}))
    S = shap_values(boosters, X)
    imp, groups = importance_tables(S, list(X.columns))
    split = "by_repo" if args.bundle == "new_repos" else "by_pr"
    out = os.path.join(HERE, "eval", split, "shap")
    os.makedirs(out, exist_ok=True)
    imp.to_csv(os.path.join(out, "shap_importance.csv"), index=False)
    groups.to_csv(os.path.join(out, "shap_groups.csv"), index=False)
    charts(S, X, imp, out, f"{args.bundle} bundle, {len(X):,} PRs")
    print(f"SHAP for the LightGBM member of {args.bundle} ({len(boosters)} seeds) on {len(X):,} PRs -> {out}")
    print(imp.head(15)[["feature", "group", "share"]].to_string(index=False))
    print("\nBy group:")
    print(groups[["group", "share"]].to_string(index=False))


if __name__ == "__main__":
    sys.exit(main())
