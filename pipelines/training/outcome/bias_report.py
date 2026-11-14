"""
bias_report.py - how the outcome model does on slices of PRs, from the predictions the notebook wrote.

    python bias_report.py --data riffle_mined_v0.1.zip      # reads eval/by_pr and eval/by_repo predictions.csv

Writes eval/<split>/bias/slices.csv and slices.png. The three seeds' probabilities are averaged per PR.

Slices, all known at open: first PR by this author in this repo or not (the newcomer question), fork or branch in
the repo, an AI-agent branch prefix or not, PR size quartile (lines added + deleted), the miner version that mined
the row (6.x or 7.x), and repo.

Per slice: PRs, accuracy, macro F1, the actual and predicted goes-nowhere rates, and the false-flag rate (the share
of PRs that landed but were predicted to go nowhere). A slice is flagged when its accuracy is more than 0.05 below
the overall accuracy, or its false-flag rate is more than 1.25 times the overall rate (and 0.02 above it), with at
least 200 PRs.
"""
from __future__ import annotations

import argparse
import io
import os
import sys
import zipfile

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))          # pipelines/training, for outcome_features.py
import outcome_features as OF  # noqa: E402

CLASSES = OF.CLASSES
MIN_PRS = 200


def read_dataset(path):
    if path.endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            name = next(n for n in z.namelist() if n.endswith("dataset_merged.parquet"))
            return pd.read_parquet(io.BytesIO(z.read(name)))
    return pd.read_parquet(path)


def slice_columns(raw):
    d = OF.build_features(raw)
    size = pd.to_numeric(d["LA"], errors="coerce").fillna(0) + pd.to_numeric(d["LD"], errors="coerce").fillna(0)
    out = pd.DataFrame({"repo": d["repo"], "pr_number": d["pr_number"]})
    out["author"] = np.where(d["author_key"].isna(), "unknown author",
                             np.where(d["ah_first_pr"] == 1, "first PR in repo", "returning"))
    out["fork"] = np.where(d["head_from_fork"] == 1, "fork", "branch in repo")
    agent = OF.flag(d["head_branch_agent_prefix"]) if "head_branch_agent_prefix" in d else np.zeros(len(d))
    out["agent_branch"] = np.where(agent == 1, "agent branch", "other branch")
    out["size"] = pd.qcut(size.rank(method="first"), 4, labels=["smallest 25%", "25-50%", "50-75%", "largest 25%"])
    out["miner_version"] = (d["miner_version"].astype(str).str.split(".").str[0] + ".x") if "miner_version" in d \
        else "unknown"
    return out


def macro_f1(y, yhat):
    f = []
    for k in range(3):
        tp = np.sum((yhat == k) & (y == k))
        p, r = tp / max(np.sum(yhat == k), 1), tp / max(np.sum(y == k), 1)
        f.append(0.0 if p + r == 0 else 2 * p * r / (p + r))
    return float(np.mean(f))


def metrics(g):
    y = g["outcome_3class"].map({c: i for i, c in enumerate(CLASSES)}).to_numpy()
    P = g[["p_clean", "p_rework", "p_nowhere"]].to_numpy()
    yhat = P.argmax(1)
    landed = y < 2
    return pd.Series({
        "prs": len(g), "accuracy": float(np.mean(yhat == y)), "macro_f1": macro_f1(y, yhat),
        "actual_nowhere": float(np.mean(y == 2)), "predicted_nowhere": float(P[:, 2].mean()),
        "false_flag_rate": float(np.mean(yhat[landed] == 2)) if landed.any() else np.nan,
    })


def report(split, slices, out_dir):
    p = pd.read_csv(os.path.join(HERE, "eval", split, "predictions.csv"),
                    usecols=["repo", "pr_number", "outcome_3class", "p_clean", "p_rework", "p_nowhere"])
    p = p.groupby(["repo", "pr_number", "outcome_3class"], as_index=False)[["p_clean", "p_rework", "p_nowhere"]].mean()
    p = p.merge(slices, on=["repo", "pr_number"], how="left")
    overall = metrics(p)
    rows = [dict(slice="all", group="all", **overall)]
    for col in ("author", "fork", "agent_branch", "size", "miner_version", "repo"):
        for grp, g in p.groupby(col, observed=True):
            rows.append(dict(slice=col, group=str(grp), **metrics(g)))
    t = pd.DataFrame(rows)
    big = t["prs"] >= MIN_PRS
    t["flag"] = big & ((t["accuracy"] < overall["accuracy"] - 0.05) |
                       ((t["false_flag_rate"] > 1.25 * overall["false_flag_rate"]) &
                        (t["false_flag_rate"] > overall["false_flag_rate"] + 0.02)))
    os.makedirs(out_dir, exist_ok=True)
    t.to_csv(os.path.join(out_dir, "slices.csv"), index=False)
    chart(t[t["slice"] != "repo"], overall, split, os.path.join(out_dir, "slices.png"))
    return t


def chart(t, overall, split, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    ink, muted, grid, blue = "#0b0b0b", "#52514e", "#e4e3df", "#2a78d6"
    t = t[t["slice"] != "all"].iloc[::-1]
    labels = [f"{s.replace('_', ' ')}: {g}  (n={n:,})" for s, g, n in zip(t["slice"], t["group"], t["prs"].astype(int))]
    fig, axes = plt.subplots(1, 2, figsize=(10, 0.34 * len(t) + 1.3), sharey=True, facecolor="white")
    for ax, col, title in ((axes[0], "accuracy", "Accuracy"), (axes[1], "false_flag_rate",
                                                                  "False flags: landed, predicted nowhere")):
        ax.barh(range(len(t)), t[col], height=0.6, color=blue)
        ax.axvline(overall[col], color=muted, lw=1, ls="--")
        ax.text(overall[col], len(t) - 0.3, f" all {overall[col]:.2f}", color=muted, fontsize=8, va="bottom")
        for i, v in enumerate(t[col]):
            ax.text(v, i, f" {v:.2f}", va="center", fontsize=8, color=ink)
        ax.set_title(title, fontsize=10, color=ink, loc="left")
        ax.set_xlim(0, max(t[col].max() * 1.2, overall[col] * 1.3))
        ax.grid(axis="x", color=grid, lw=0.6)
        ax.set_axisbelow(True)
        for s in ("top", "right", "left"):
            ax.spines[s].set_visible(False)
        ax.tick_params(colors=muted, labelsize=8, length=0)
    axes[0].set_yticks(range(len(t)), labels, fontsize=8, color=ink)
    fig.suptitle(f"Outcome model by slice ({'held-out PRs' if split == 'by_pr' else 'held-out repos'})",
                 x=0.01, ha="left", fontsize=11, color=ink)
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.environ.get("RIFFLE_DATA", "riffle_mined_v0.1.zip"))
    a = ap.parse_args()
    slices = slice_columns(read_dataset(a.data))
    for split in ("by_pr", "by_repo"):
        t = report(split, slices, os.path.join(HERE, "eval", split, "bias"))
        flagged = t[t["flag"]]
        print(f"{split}: {len(t) - 1} slices, {len(flagged)} flagged")
        if len(flagged):
            print(flagged[["slice", "group", "prs", "accuracy", "false_flag_rate"]].to_string(index=False))


if __name__ == "__main__":
    main()
