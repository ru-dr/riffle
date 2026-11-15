"""
bias_mitigation.py - tries the guidelines' threshold mitigation (6.3) on the newcomer false-flag gap and measures
what it costs, from the predictions the notebook wrote. Run it after bias_report.py.

    python bias_mitigation.py --data riffle_mined_v0.1.zip     # or a dataset parquet

Reads eval/<split>/predictions.csv.

The gap: of first-time authors' PRs that landed, about a third are predicted to go nowhere, against a tenth for
returning authors. bias_report.py shows the model is calibrated for both groups, so the gap follows a real
difference in outcomes. This script tests the standard fix anyway, so the decision rests on numbers.

Policies, applied to the top guess only (the label), for newcomers only:
  none     the model's top guess, as shipped
  unflag   'goes nowhere' needs a higher bar for newcomers, just enough to bring their false-flag rate under the
           level at which bias_report.py flags a slice (1.25 x the overall rate, or 0.02 above it)
  parity   a higher bar still: newcomers' false-flag rate down to the returning authors' rate
The bar is a multiplier w on p_nowhere in the arg-max (w < 1 for newcomers). Probabilities are not changed, so
calibration stays as it is; the 'shifted' rows show what happens if the same multiplier is instead applied to the
probabilities themselves (renormalised), which is what an equal-treatment score would require.

Per policy: newcomers' and returning authors' false-flag rates, the share of newcomers' goes-nowhere PRs still
caught (recall), newcomers' and overall accuracy and macro F1, and for the shifted variant the newcomers'
calibration error on goes nowhere (10 equal-size bins).
Writes eval/<split>/bias/mitigation.csv and mitigation.md.
"""
from __future__ import annotations

import argparse
import os

import numpy as np
import pandas as pd

import bias_report as BR

HERE = os.path.dirname(os.path.abspath(__file__))
NEWCOMER, RETURNING = "first PR in repo", "returning"


def load_predictions(split):
    p = pd.read_csv(os.path.join(HERE, "eval", split, "predictions.csv"),
                    usecols=["repo", "pr_number", "outcome_3class", "p_clean", "p_rework", "p_nowhere"])
    cols = ["p_clean", "p_rework", "p_nowhere"]
    return p.groupby(["repo", "pr_number", "outcome_3class"], as_index=False)[cols].mean()


def decide(P, is_new, w):
    """Top guess with p_nowhere multiplied by w for newcomers (decision only)."""
    Q = P.copy()
    Q[is_new, 2] *= w
    return Q.argmax(1)


def false_flag(y, yhat, mask):
    landed = mask & (y < 2)
    return float(np.mean(yhat[landed] == 2)) if landed.any() else np.nan


def calibration_error(p, hit, bins=10):
    """Mean |predicted - actual| over equal-size bins, weighted by bin size."""
    order = np.argsort(p)
    parts = np.array_split(order, bins)
    return float(sum(len(b) * abs(p[b].mean() - hit[b].mean()) for b in parts if len(b)) / len(p))


def summarise(name, y, P, yhat, is_new, is_ret, w, shifted=False):
    nw = is_new & (y == 2)
    row = dict(policy=name, multiplier=round(w, 4),
               ff_newcomer=false_flag(y, yhat, is_new), ff_returning=false_flag(y, yhat, is_ret),
               ff_all=false_flag(y, yhat, np.ones(len(y), bool)),
               nowhere_recall_newcomer=float(np.mean(yhat[nw] == 2)) if nw.any() else np.nan,
               acc_newcomer=float(np.mean(yhat[is_new] == y[is_new])), acc_all=float(np.mean(yhat == y)),
               macro_f1_all=BR.macro_f1(y, yhat))
    if shifted:
        Q = P[is_new].copy()
        Q[:, 2] *= w
        Q /= Q.sum(1, keepdims=True)
        row["calibration_error_newcomer_nowhere"] = calibration_error(Q[:, 2], (y[is_new] == 2).astype(float))
    else:
        row["calibration_error_newcomer_nowhere"] = calibration_error(P[is_new, 2], (y[is_new] == 2).astype(float))
    return row


def find_multiplier(y, P, is_new, target):
    """The largest w in (0, 1] whose newcomer false-flag rate is at most target (bisection)."""
    if false_flag(y, decide(P, is_new, 1.0), is_new) <= target:
        return 1.0
    lo, hi = 0.0, 1.0
    for _ in range(40):
        mid = (lo + hi) / 2
        if false_flag(y, decide(P, is_new, mid), is_new) <= target:
            lo = mid
        else:
            hi = mid
    return lo


def mitigate(pred, slices):
    d = pred.merge(slices[["repo", "pr_number", "author"]], on=["repo", "pr_number"], how="left")
    y = d["outcome_3class"].map({c: i for i, c in enumerate(BR.CLASSES)}).to_numpy()
    P = d[["p_clean", "p_rework", "p_nowhere"]].to_numpy(float)
    is_new, is_ret = (d["author"] == NEWCOMER).to_numpy(), (d["author"] == RETURNING).to_numpy()
    base = decide(P, is_new, 1.0)
    overall = false_flag(y, base, np.ones(len(y), bool))
    targets = {"unflag": max(1.25 * overall, overall + 0.02) - 1e-6, "parity": false_flag(y, base, is_ret)}
    rows = [summarise("none", y, P, base, is_new, is_ret, 1.0)]
    for name, t in targets.items():
        w = find_multiplier(y, P, is_new, t)
        rows.append(summarise(name, y, P, decide(P, is_new, w), is_new, is_ret, w))
        rows.append(summarise(f"{name} (shifted probabilities)", y, P, decide(P, is_new, w), is_new, is_ret, w,
                              shifted=True))
    return pd.DataFrame(rows), dict(prs=len(d), newcomers=int(is_new.sum()), returning=int(is_ret.sum()),
                                    targets=targets)


def as_markdown(split, t, info):
    pct = lambda v: "—" if pd.isna(v) else f"{v:.1%}"  # noqa: E731
    num = lambda v: "—" if pd.isna(v) else f"{v:.3f}"  # noqa: E731
    lines = [f"### Newcomer threshold mitigation ({split.replace('_', ' ')}; {info['prs']:,} PRs, "
             f"{info['newcomers']:,} by newcomers)", "",
             "| policy | p_nowhere multiplier | false flags, newcomers | false flags, returning | nowhere caught, "
             "newcomers | accuracy, newcomers | accuracy, all | macro F1, all | calibration error, newcomers |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in t.to_dict("records"):
        lines.append(f"| {r['policy']} | {r['multiplier']:.3f} | {pct(r['ff_newcomer'])} | {pct(r['ff_returning'])} | "
                     f"{pct(r['nowhere_recall_newcomer'])} | {pct(r['acc_newcomer'])} | {pct(r['acc_all'])} | "
                     f"{num(r['macro_f1_all'])} | {num(r['calibration_error_newcomer_nowhere'])} |")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", default=os.environ.get("RIFFLE_DATA", "riffle_mined_v0.1.zip"))
    a = ap.parse_args()
    slices = BR.slice_columns(BR.read_dataset(a.data))
    for split in ("by_repo", "by_pr"):
        t, info = mitigate(load_predictions(split), slices)
        out = os.path.join(HERE, "eval", split, "bias")
        os.makedirs(out, exist_ok=True)
        t.to_csv(os.path.join(out, "mitigation.csv"), index=False)
        md = as_markdown(split, t, info)
        with open(os.path.join(out, "mitigation.md"), "w", encoding="utf-8") as fh:
            fh.write(md)
        print(md)


if __name__ == "__main__":
    main()
