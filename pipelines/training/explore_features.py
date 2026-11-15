#!/usr/bin/env python
"""
explore_features.py - understand the mined data and try feature engineering before building the model.

    python pipelines/training/explore_features.py
    python pipelines/training/explore_features.py --data pipelines/training/data/dataset_merged.parquet --out data/feature_report

Reads the three mined tables through frames.py: the dataset table (one row per closed PR, with its at-open
features), the snapshot table (the same PRs at moments while they were open) and the commit features. It prints a
report, saves the same text as report.txt, and writes tables (CSV), charts (PNG) and features_recommended.txt to
--out. With --save-features it also writes the at-open rows with the engineered features, ready for modelling.

Sections: data check, what is in the data, snapshots and commit features, label quality, what the features look
like, how much each feature tells us, features that repeat each other, features that change by repo or over time,
engineered features, a quick model check (needs lightgbm or scikit-learn) and recommendations.

The analysis rows are the plan's training rows: PRs with an outcome, by outside contributors, no bots, and old enough
for the outcome to be settled. --strict-outside also drops authors who had already merged or closed someone else's
PR (author_confirmed_maintainer); --since keeps only PRs opened on or after a date.

Needs numpy, pandas, pyarrow and matplotlib; lightgbm or scikit-learn is optional (the quick model check).
The default --out, data/feature_report at the repo root, is gitignored.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import warnings

import numpy as np
import pandas as pd

try:                                   # required: the report draws charts
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError:                    # pragma: no cover
    sys.exit("explore_features.py needs matplotlib for its charts. Install it with: pip install matplotlib")

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import frames  # noqa: E402  the team's loader: joins the tables and knows which columns are never inputs

REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
CLASSES = ("lands_clean", "lands_after_rework", "goes_nowhere")
SHORT = ("clean", "rework", "nowhere")
REASONS = ("abandoned", "duplicate", "unwanted", "ci_technical", "unknown")
OUTSIDE = ("CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR", "FIRST_TIMER", "NONE")
FIRST_TIME = ("FIRST_TIME_CONTRIBUTOR", "FIRST_TIMER")
CONTRACTS = {"dataset": "mined_dataset.schema.json", "snapshots": "mined_snapshot.schema.json",
             "commit_features": "mined_commit_features.schema.json"}
GROUP_NAMES = {
    "mine_repo": "PR and author basics", "diff_stats": "diff size and shape", "history": "file and repo history",
    "line_history": "history of the changed lines", "repo_local": "repo-specific path history",
    "merge_signals": "description, similar PRs, repo track record", "maintainers": "maintainer status",
    "llm_flags": "LLM flags", "deterministic": "rule-based diff checks",
    "config": "files touched (auth, CI, infra, lockfile, migrations)", "scanners": "static scanners",
    "deps_cve": "dependencies and vulnerabilities", "api_contract": "API contract", "flaky": "CI flakiness",
    "snapshot": "PR activity so far (snapshots only)", "engineered": "engineered here", "other": "other"}
PLAN = dict(since="2023-07-01", cap=8000, max_bot_share=0.5, min_unmerged=0.15, max_unmerged=0.75)


class Settings:
    max_rows = 200_000      # rows used to measure signal (sampled above this)
    corr_rows = 30_000      # rows used to find features that repeat each other
    model_rows = 150_000    # training rows for the quick model check
    min_rows = 200          # a group or feature needs this many rows to be judged
    empty = 0.05            # filled in for less than 5% of rows
    near_constant = 0.99    # one value covers 99%+ of the rows
    heavy_skew = 3.0
    suspicious = 0.85       # an AUROC this high on its own usually means a leak
    redundant = 0.90        # rank correlation at which two features say the same thing
    repo_share = 0.50       # share of a feature's spread explained by which repo it is
    drift = 0.25            # population stability index above this is a big shift
    maturity_days = 90


# ===================================================================== small helpers
def _section(title):
    print("\n" + "=" * 100 + f"\n{title}\n" + "=" * 100)


def _table(df, nd=3):
    if df is None or len(df) == 0:
        return "(nothing to show)"
    with pd.option_context("display.width", 220, "display.max_columns", 40, "display.max_rows", 500,
                           "display.max_colwidth", 70, "display.float_format", ("{:." + str(nd) + "f}").format):
        return df.to_string(index=False)


def _pct(v):
    return "n/a" if v is None or not np.isfinite(v) else f"{v:.1%}"


def _n(count, word):
    return f"{count:,} {word}{'' if count == 1 else 's'}"


def _safe(name):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(name))


def _true(df, col):
    """Rows where a yes/no column is True (False where it is missing or the column is absent)."""
    if col not in df.columns:
        return np.zeros(len(df), bool)
    return np.array(df[col].eq(True).fillna(False), dtype=bool)


def _num(s: pd.Series):
    """The column as floats (yes/no becomes 1/0), or None when it is text."""
    if pd.api.types.is_bool_dtype(s) or pd.api.types.is_numeric_dtype(s):
        return s.astype("Float64").to_numpy(dtype=float, na_value=np.nan)
    if s.dtype == object:
        v = s.dropna()
        if len(v) == 0:
            return np.full(len(s), np.nan)
        head = v.iloc[:1000]
        if all(isinstance(x, (bool, np.bool_)) for x in head):
            return s.map({True: 1.0, False: 0.0}).to_numpy(dtype=float)
        if all(isinstance(x, (int, float, np.integer, np.floating)) and not isinstance(x, bool) for x in head):
            return pd.to_numeric(s, errors="coerce").to_numpy(float)
    return None


def _keys(df):
    num = pd.to_numeric(df["pr_number"], errors="coerce").round().astype("Int64").astype(str)
    return (df["repo"].astype(str).str.strip().str.lower() + "#" + num).to_numpy()


def _times(s):
    if pd.api.types.is_datetime64_any_dtype(s):
        return pd.to_datetime(s, utc=True)
    try:
        t = pd.to_datetime(s, utc=True, errors="coerce", format="ISO8601")
    except (TypeError, ValueError):
        t = pd.to_datetime(s, utc=True, errors="coerce")
    if t.isna().all() and s.notna().any():
        t = pd.to_datetime(s, utc=True, errors="coerce")
    return t


def auroc(yb, x):
    """Chance that a row with the outcome has a higher value than one without it (0.5 = no signal)."""
    yb = np.asarray(yb, bool)
    n1 = int(yb.sum())
    n0 = len(yb) - n1
    if n1 == 0 or n0 == 0:
        return np.nan
    r = pd.Series(x).rank(method="average").to_numpy()
    return float((r[yb].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def psi(a, b, bins=10):
    """Population stability index: how different two samples of a feature are (0 = same, above 0.25 = a big shift)."""
    a, b = a[np.isfinite(a)], b[np.isfinite(b)]
    if len(a) < 100 or len(b) < 100:
        return np.nan
    edges = np.unique(np.quantile(a, np.linspace(0, 1, bins + 1)))
    if len(edges) < 2:
        return 0.0
    edges[0], edges[-1] = -np.inf, np.inf
    pa = np.clip(np.histogram(a, edges)[0] / len(a), 1e-4, None)
    pb = np.clip(np.histogram(b, edges)[0] / len(b), 1e-4, None)
    return float(np.sum((pa - pb) * np.log(pa / pb)))


def brier(P, y):
    Y = np.zeros_like(P)
    Y[np.arange(len(y)), y] = 1.0
    return float(((P - Y) ** 2).sum(axis=1).mean())


class _Tee:
    def __init__(self, path, stream):
        self.file = open(path, "w", encoding="utf-8")
        self.stream = stream

    def write(self, s):
        self.stream.write(s)
        self.file.write(s)

    def flush(self):
        self.stream.flush()
        self.file.flush()

    def close(self):
        self.file.close()


# ===================================================================== contract and groups
def contract_props(name):
    try:
        return frames._schema_props(name)
    except Exception:
        return {}


def feature_groups(cols):
    """Which miner module makes each column, from the contract's 'Computed in `...`' note."""
    groups = {}
    for table in ("dataset", "snapshots"):
        for col, spec in contract_props(CONTRACTS[table]).items():
            desc = spec.get("description", "") if isinstance(spec, dict) else ""
            m = re.search(r"Computed in `([^`]+)`", desc)
            if m:
                groups.setdefault(col, os.path.basename(m.group(1)).replace(".py", ""))
            elif desc.startswith("Snapshot state"):
                groups.setdefault(col, "snapshot")
    return {c: groups.get(c, "other") for c in cols}


# ===================================================================== 0. data check
def sha256(path, block=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(block), b""):
            h.update(chunk)
    return h.hexdigest()


def data_check(data_path, t):
    out = []
    folder = os.path.dirname(os.path.abspath(data_path))
    man_path = os.path.join(folder, "MANIFEST.json")
    manifest = None
    if os.path.isfile(man_path):
        with open(man_path, encoding="utf-8") as fh:
            manifest = json.load(fh)
    tables = {"dataset": t.dataset, "snapshots": t.snapshots, "commit_features": t.commit_features}
    for name, df in tables.items():
        rec = dict(table=name, rows=None if df is None else len(df), columns=None if df is None else df.shape[1])
        if manifest and name in manifest.get("files", {}):
            f = manifest["files"][name]
            path = os.path.join(folder, f["name"])
            rec["rows_in_manifest"] = f.get("rows")
            rec["checksum"] = ("OK" if os.path.isfile(path) and sha256(path) == f.get("sha256") else "DIFFERENT") \
                if f.get("sha256") else "n/a"
        props = contract_props(CONTRACTS[name])
        if df is not None and props:
            missing = sorted(set(props) - set(df.columns))
            extra = sorted(set(df.columns) - set(props))
            rec["missing_vs_contract"] = ", ".join(missing[:6]) + (" ..." if len(missing) > 6 else "") if missing else "none"
            rec["extra_vs_contract"] = ", ".join(extra[:6]) + (" ..." if len(extra) > 6 else "") if extra else "none"
        if df is not None and len(df):
            key = ["repo", "pr_number"] + (["snapshot_id"] if name == "snapshots" and "snapshot_id" in df.columns
                                           else ["head_sha"] if name == "commit_features" and "head_sha" in df.columns
                                           else [])
            rec["duplicate_keys"] = int(df.duplicated(key[-1:] if name == "snapshots" else key).sum())
        out.append(rec)
    repos = {n: set(df["repo"].astype(str).str.lower()) for n, df in tables.items() if df is not None and len(df)}
    same_repos = len({frozenset(v) for v in repos.values()}) <= 1
    return pd.DataFrame(out), manifest, same_repos


# ===================================================================== population
def build_population(d, args, S, notes):
    """The analysis rows, with a count for each step so the effect of each rule is visible."""
    steps = [("all PRs", len(d))]
    keep = np.array(d["outcome_3class"].isin(CLASSES), dtype=bool)        # a writable copy (pandas 3 arrays are read-only)
    steps.append(("with an outcome", int(keep.sum())))
    if "author_association" in d.columns:
        keep &= np.array(d["author_association"].isin(OUTSIDE), dtype=bool)
        steps.append(("by outside contributors (author_association)", int(keep.sum())))
    keep &= ~_true(d, "author_is_bot")
    steps.append(("not bots", int(keep.sum())))
    maint = _true(d, "author_confirmed_maintainer")
    steps.append(("  of which the author had already merged or closed others' PRs", int((keep & maint).sum())))
    if args.strict_outside:
        keep &= ~maint
        steps.append(("without those authors (--strict-outside)", int(keep.sum())))
    created = _times(d["created_at"]) if "created_at" in d.columns else pd.Series(pd.NaT, index=d.index)
    if "observed_through" in d.columns:
        young = np.array(((_times(d["observed_through"]) - created).dt.days < S.maturity_days).fillna(False), dtype=bool)
        keep &= ~young
        steps.append((f"old enough for a settled outcome ({S.maturity_days}+ days)", int(keep.sum())))
    if args.since:
        keep &= np.array((created >= pd.Timestamp(args.since, tz="UTC")).fillna(False), dtype=bool)
        steps.append((f"opened on or after {args.since} (--since)", int(keep.sum())))
    p = d[keep].copy()
    p["y"] = p["outcome_3class"].map({c: i for i, c in enumerate(CLASSES)}).astype(int)
    p["_created"] = created[keep].to_numpy()
    p["_key"] = _keys(p)
    if hasattr(frames, "_mask_final") and "open_snapshot" in p.columns:
        final = int((p["open_snapshot"] == "final").sum())
        if final:
            notes.append(f"{_n(final, 'PR')} had no recoverable at-open diff (open_snapshot = final): frames.py blanks "
                         "their diff features, and so does this report")
        p = frames._mask_final(p)
    return p.reset_index(drop=True), pd.DataFrame(steps, columns=["step", "PRs"])


# ===================================================================== 1. what is in the data
def overview(d, p):
    created = _times(d["created_at"])
    by_year = pd.DataFrame({"year": created.dt.year, "outcome": d["outcome_3class"]}).dropna(subset=["year"])
    year_tab = by_year.groupby("year").size().rename("all_prs").to_frame()
    py = pd.DataFrame({"year": p["_created"].dt.year, "y": p["y"]})
    mix = py.groupby("year")["y"].value_counts(normalize=True).unstack().reindex(columns=range(3))
    mix.columns = list(CLASSES)
    year_tab = year_tab.join(py.groupby("year").size().rename("analysis_prs")).join(mix).reset_index()
    year_tab["year"] = year_tab["year"].astype(int)
    g = d.assign(bot=_true(d, "author_is_bot"), agent=_true(d, "ai_agent_authored"),
                 outside=d["author_association"].isin(OUTSIDE) if "author_association" in d.columns else False)
    repo = g.groupby("repo").agg(prs=("pr_number", "size"), bot_share=("bot", "mean"), agent_prs=("agent", "sum"))
    pm = p.groupby("repo")["y"].value_counts(normalize=True).unstack().reindex(columns=range(3))
    pm.columns = list(CLASSES)
    repo = repo.join(p.groupby("repo").size().rename("analysis_prs")).join(pm).reset_index()
    repo["analysis_prs"] = repo["analysis_prs"].fillna(0).astype(int)
    flags = []
    for _, r in repo.iterrows():
        f = []
        if r["prs"] > PLAN["cap"]:
            f.append(f"over the {PLAN['cap']:,}-PR cap")
        if r["bot_share"] > PLAN["max_bot_share"]:
            f.append("more than half bot PRs")
        if np.isfinite(r.get("goes_nowhere", np.nan)) and not (PLAN["min_unmerged"] <= r["goes_nowhere"] <= PLAN["max_unmerged"]):
            f.append(f"goes-nowhere share outside {PLAN['min_unmerged']:.0%}-{PLAN['max_unmerged']:.0%}")
        flags.append("; ".join(f))
    repo["outside_the_plan"] = flags
    return year_tab, repo.sort_values("prs", ascending=False).reset_index(drop=True), created


# ===================================================================== 2. snapshots and commit features
def coverage(d, t):
    """Which PRs have snapshots and commit features, to find out why some do not."""
    dk = _keys(d)
    snap_keys = set(_keys(t.snapshots)) if t.snapshots is not None and len(t.snapshots) else set()
    commit_keys = set(_keys(t.commit_features)) if t.commit_features is not None and len(t.commit_features) else set()
    c = pd.DataFrame({"repo": d["repo"].to_numpy(), "has_snapshots": [k in snap_keys for k in dk],
                      "has_commit_features": [k in commit_keys for k in dk], "created": _times(d["created_at"]).to_numpy(),
                      "merged": _true(d, "label_merged"), "bot": _true(d, "author_is_bot"),
                      "outside": d["author_association"].isin(OUTSIDE).to_numpy() if "author_association" in d.columns
                      else np.ones(len(d), bool), "has_outcome": d["outcome_3class"].notna().to_numpy()})
    c["year"] = pd.to_datetime(c["created"], utc=True).dt.year
    per_repo = c.groupby("repo").agg(prs=("has_snapshots", "size"), with_snapshots=("has_snapshots", "mean"),
                                     with_commit_features=("has_commit_features", "mean"))
    first = c[c["has_snapshots"]].groupby("repo")["created"].min()
    per_repo = per_repo.join(first.rename("first_pr_with_snapshots"))
    after = []
    for repo, row in per_repo.iterrows():
        f = row["first_pr_with_snapshots"]
        sub = c[(c["repo"] == repo) & (c["created"] >= f)] if pd.notna(f) else c.iloc[0:0]
        after.append(sub["has_snapshots"].mean() if len(sub) else np.nan)
    per_repo["with_snapshots_since_then"] = after
    per_repo = per_repo.reset_index().sort_values("prs", ascending=False)
    per_repo["first_pr_with_snapshots"] = pd.to_datetime(per_repo["first_pr_with_snapshots"], utc=True).dt.strftime("%Y-%m-%d")
    cuts = []
    for name, col in (("opened per year", "year"), ("outside contributor", "outside"), ("bot", "bot"),
                      ("merged", "merged"), ("has an outcome", "has_outcome")):
        t2 = c.groupby(col)[["has_snapshots", "has_commit_features"]].mean().reset_index().rename(columns={col: "value"})
        t2.insert(0, "split_by", name)
        t2["value"] = t2["value"].astype(str)
        cuts.append(t2)
    return per_repo.reset_index(drop=True), pd.concat(cuts, ignore_index=True), c


def snapshot_summary(t):
    s = t.snapshots
    if s is None or len(s) == 0:
        return None
    k = ["repo", "pr_number"]
    per = s.groupby(k).size()
    out = dict(snapshot_rows=len(s), prs_with_snapshots=len(per), per_pr_average=float(per.mean()),
               per_pr_median=float(per.median()), per_pr_max=int(per.max()), prs_with_one=float((per == 1).mean()))
    if "near_decided" in s.columns:
        out["near_decided_share"] = float(_true(s, "near_decided").mean())
    if "snapshot_weight" in s.columns:
        w = pd.to_numeric(s["snapshot_weight"], errors="coerce")
        sums = w.groupby([s["repo"], s["pr_number"]]).sum()
        out["weights_add_to_1"] = float(((sums - 1).abs() < 1e-6).mean())
        if "near_decided" in s.columns:
            kept = ~_true(s, "near_decided")
            sums2 = w[kept].groupby([s.loc[kept, "repo"], s.loc[kept, "pr_number"]]).sum()
            out["weights_add_to_1_without_near_decided"] = float(((sums2 - 1).abs() < 1e-6).mean())
    triggers = s["trigger"].value_counts().rename_axis("trigger").reset_index(name="snapshots") if "trigger" in s.columns else None
    return out, triggers


# ===================================================================== 3. label quality
def label_quality(d, p):
    out = {}
    if {"label_merged", "label_rework"} <= set(d.columns):
        exp = np.where(~_true(d, "label_merged") & d["label_merged"].notna().to_numpy(), "goes_nowhere",
                       np.where(_true(d, "label_rework"), "lands_after_rework",
                                np.where(_true(d, "label_merged"), "lands_clean", "?")))
        ok = d["outcome_3class"].notna().to_numpy()
        out["outcome_disagrees_with_merged_and_rework"] = int((d["outcome_3class"].to_numpy()[ok] != exp[ok]).sum())
    if "nowhere_reason" in d.columns:
        out["reason_on_prs_that_did_not_go_nowhere"] = int((d["nowhere_reason"].notna() & (d["outcome_3class"] != "goes_nowhere")).sum())
        nw = p[p["outcome_3class"] == "goes_nowhere"]
        out["reasons"] = nw["nowhere_reason"].value_counts(dropna=False, normalize=True).round(3).to_dict()
        if "closed_at" in p.columns:
            ab = nw[nw["nowhere_reason"] == "abandoned"]
            hours = (_times(ab["closed_at"]) - ab["_created"]).dt.total_seconds() / 3600
            hours = hours.dropna()
            if len(hours):
                out["abandoned_median_hours_open"] = float(hours.median())
                out["abandoned_closed_within_1_hour"] = float((hours < 1).mean())
                out["abandoned_closed_within_1_day"] = float((hours < 24).mean())
    if "close_reason_source" in p.columns:
        out["reason_source"] = p.loc[p["outcome_3class"] == "goes_nowhere", "close_reason_source"].value_counts(dropna=False).to_dict()
    m = _true(d, "label_merged")
    after = {}
    for col, obs in (("label_bad", "label_bad_observed"), ("label_ci_fail", "ci_observed"), ("label_revert", "revert_window_closed"),
                     ("label_hotfix", "hotfix_window_closed"), ("label_szz_bug", "szz_window_closed")):
        if col not in d.columns:
            continue
        v = d.loc[m, col]
        o = _true(d.loc[m], obs) if obs in d.columns else np.ones(m.sum(), bool)
        after[col] = dict(merged_prs=int(m.sum()), observed=float(o.mean()), yes_where_observed=float((v[o] == True).mean()) if o.any() else np.nan,  # noqa: E712
                          yes_where_not_observed=int((v[~o] == True).sum()), no_where_not_observed=int((v[~o] == False).sum()))  # noqa: E712
    out["after_merge"] = pd.DataFrame([dict(label=k, **v) for k, v in after.items()])
    llm = [c for c in ("change_intent", "semantic_risk_1to5", "refactor_vs_behavior", "security_sensitive_ops") if c in p.columns]
    if llm:
        out["prs_without_llm_flags"] = float(p[llm].isna().all(axis=1).mean())
    if "llm_prompt_version" in p.columns:
        out["llm_prompt_versions"] = p["llm_prompt_version"].value_counts(dropna=False).head(5).to_dict()
    if "head_sha" in d.columns:
        hs = d.dropna(subset=["head_sha"])
        out["prs_sharing_a_head_commit"] = int(hs["head_sha"].duplicated(keep=False).sum())
    return out


# ===================================================================== 4. what the features look like
def profile(p, cols, groups, S):
    recs = []
    for c in cols:
        s = p[c]
        x = _num(s)
        rec = dict(feature=c, group=GROUP_NAMES.get(groups[c], groups[c]), filled=float(s.notna().mean()))
        if x is not None:
            v = x[np.isfinite(x)]
            uniq = np.unique(v)
            rec["kind"] = "yes/no" if len(uniq) and set(uniq.tolist()) <= {0.0, 1.0} else "number"
            rec["distinct"] = int(len(uniq))
            if len(v):
                vals, counts = np.unique(v, return_counts=True)
                rec["top_value_share"] = float(counts.max() / len(v))
                rec["zero_share"] = float((v == 0).mean())
                rec["median"] = float(np.median(v))
                rec["p99"] = float(np.quantile(v, 0.99))
                rec["max"] = float(v.max())
                rec["skew"] = float(pd.Series(v).skew()) if len(uniq) > 2 else np.nan
                rec["min"] = float(v.min())
        else:
            vc = s.dropna().astype(str).value_counts()
            rec["kind"] = "text"
            rec["distinct"] = int(len(vc))
            rec["top_value_share"] = float(vc.iloc[0] / vc.sum()) if len(vc) else np.nan
            rec["examples"] = ", ".join(vc.index[:3].tolist())[:60]
        notes = []
        if rec["filled"] < S.empty:
            notes.append("empty")
        elif rec["distinct"] <= 1:
            notes.append("constant")
        elif rec.get("top_value_share", 0) >= S.near_constant:
            notes.append("nearly constant")
        if rec["kind"] == "number" and rec.get("min", -1) >= 0 and rec.get("skew", 0) > S.heavy_skew:
            notes.append("long tail")
        if rec["kind"] == "text":
            notes.append("text: needs encoding" if rec["distinct"] <= 30 else "text with many values")
        rec["notes"] = ", ".join(notes)
        recs.append(rec)
    cols_out = ["feature", "group", "kind", "filled", "distinct", "top_value_share", "zero_share", "median", "p99",
                "max", "skew", "notes", "examples"]
    return pd.DataFrame(recs).reindex(columns=cols_out)


# ===================================================================== 5. how much each feature tells us
def strength(a):
    if not np.isfinite(a):
        return ""
    if a > Settings.suspicious:
        return "suspicious"
    return "strong" if a >= 0.65 else "some" if a >= 0.58 else "weak" if a >= 0.53 else "none"


def signal(p, cols, groups, S):
    y = p["y"].to_numpy()
    recs = []
    for c in cols:
        s = p[c]
        x = _num(s)
        rec = dict(feature=c, group=GROUP_NAMES.get(groups[c], groups[c]))
        miss = s.isna().to_numpy()
        if S.empty <= miss.mean() <= 1 - S.empty and miss.sum() >= S.min_rows and (~miss).sum() >= S.min_rows:
            rec["nowhere_if_missing"] = float((y[miss] == 2).mean())
            rec["nowhere_if_filled"] = float((y[~miss] == 2).mean())
            rec["missing_says_something"] = abs(rec["nowhere_if_missing"] - rec["nowhere_if_filled"]) >= 0.05
        if x is None:
            vc = s.dropna().astype(str)
            cats = vc.value_counts()
            cats = cats[cats >= S.min_rows]
            if 2 <= len(cats) <= 30:
                rates = pd.Series(y[s.notna().to_numpy()] == 2, index=vc.index).groupby(vc).mean().reindex(cats.index)
                rec["nowhere_rate_spread"] = float(rates.max() - rates.min())
                rec["direction"] = f"most nowhere: {rates.idxmax()} ({rates.max():.0%}); least: {rates.idxmin()} ({rates.min():.0%})"
            recs.append(rec)
            continue
        ok = np.isfinite(x)
        if ok.sum() < S.min_rows or np.unique(x[ok]).size < 2:
            rec["direction"] = "too few values to judge"
            recs.append(rec)
            continue
        best, best_k, best_raw = 0.5, None, 0.5
        for k, sname in enumerate(SHORT):
            a = auroc(y[ok] == k, x[ok])
            rec[f"auroc_{sname}"] = a
            if np.isfinite(a) and max(a, 1 - a) > best:
                best, best_k, best_raw = max(a, 1 - a), k, a
        rec["signal"] = best
        rec["strength"] = strength(best)
        if best_k is not None:
            rec["direction"] = f"{'higher' if best_raw > 0.5 else 'lower'} -> more {CLASSES[best_k]}"
        recs.append(rec)
    out = pd.DataFrame(recs).reindex(columns=["feature", "group", "signal", "strength", "direction", "auroc_clean",
                                              "auroc_rework", "auroc_nowhere", "nowhere_if_missing",
                                              "nowhere_if_filled", "missing_says_something", "nowhere_rate_spread"])
    return out.sort_values("signal", ascending=False, na_position="last").reset_index(drop=True)


def binned_rates(p, col, bins=10):
    x = _num(p[col])
    ok = np.isfinite(x)
    if ok.sum() < 500:
        return None
    y = p["y"].to_numpy()[ok]
    xs = pd.Series(x[ok])
    if xs.nunique() <= bins:
        b = xs
    else:
        b = pd.qcut(xs.rank(method="first"), bins, labels=False)
    t = pd.DataFrame({"bin": b.to_numpy(), "x": xs.to_numpy(), "y": y}).groupby("bin").agg(
        x=("x", "median"), n=("y", "size"), clean=("y", lambda v: (v == 0).mean()), rework=("y", lambda v: (v == 1).mean()),
        nowhere=("y", lambda v: (v == 2).mean()))
    return t


# ===================================================================== 6. features that repeat each other
def redundancy(p, cols, sig, S):
    use = [c for c in cols if _num(p[c]) is not None and p[c].notna().mean() >= 0.2]
    if len(use) < 2:
        return pd.DataFrame(), pd.DataFrame(), use
    sample = p.sample(n=min(len(p), S.corr_rows), random_state=0)
    R = pd.DataFrame({c: _num(sample[c]) for c in use})
    R = R.loc[:, R.nunique() > 1]
    corr = R.rank().corr(min_periods=300)
    pairs = []
    cols_ = corr.columns.tolist()
    vals = corr.to_numpy()
    for i in range(len(cols_)):
        for j in range(i + 1, len(cols_)):
            v = vals[i, j]
            if np.isfinite(v) and abs(v) >= S.redundant:
                pairs.append(dict(feature_a=cols_[i], feature_b=cols_[j], rank_correlation=float(v)))
    pairs = pd.DataFrame(pairs)
    clusters = pd.DataFrame()
    if len(pairs):
        parent = {}

        def find(a):
            parent.setdefault(a, a)
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        for a, b in zip(pairs["feature_a"], pairs["feature_b"]):
            parent[find(a)] = find(b)
        members = {}
        for f in parent:
            members.setdefault(find(f), []).append(f)
        sigmap = sig.set_index("feature")["signal"].to_dict()
        rows = []
        for m in members.values():
            m = sorted(m, key=lambda f: -np.nan_to_num(sigmap.get(f, np.nan), nan=0))
            rows.append(dict(keep=m[0], could_drop=", ".join(m[1:]), size=len(m)))
        clusters = pd.DataFrame(rows).sort_values("size", ascending=False).reset_index(drop=True)
    return pairs.sort_values("rank_correlation", key=abs, ascending=False).reset_index(drop=True) if len(pairs) else pairs, clusters, list(corr.columns)


# ===================================================================== 7. features that change by repo or over time
def stability(p, cols, S):
    codes = pd.Categorical(p["repo"]).codes
    R = codes.max() + 1
    created = p["_created"]
    cut = created.quantile(0.5)
    newer = (created > cut).to_numpy()
    test = frames.is_test(p, by="repo", test_frac=0.2).to_numpy()
    recs = []
    for c in cols:
        x = _num(p[c])
        if x is None:
            continue
        ok = np.isfinite(x)
        rec = dict(feature=c, filled_older=float(ok[~newer].mean()) if (~newer).any() else np.nan,
                   filled_newer=float(ok[newer].mean()) if newer.any() else np.nan)
        if ok.sum() >= S.min_rows and np.unique(x[ok]).size > 1:
            r = pd.Series(x[ok]).rank().to_numpy()
            cc = codes[ok]
            mean = r.mean()
            n_g = np.bincount(cc, minlength=R)
            s_g = np.bincount(cc, weights=r, minlength=R)
            with np.errstate(invalid="ignore", divide="ignore"):
                m_g = s_g / n_g
            between = np.nansum(n_g * (m_g - mean) ** 2)
            total = ((r - mean) ** 2).sum()
            rec["repo_share"] = float(between / total) if total > 0 else np.nan
            rec["shift_older_vs_newer"] = psi(x[~newer], x[newer])
            rec["shift_train_vs_test_repos"] = psi(x[~test], x[test])
        recs.append(rec)
    out = pd.DataFrame(recs).reindex(columns=["feature", "repo_share", "shift_older_vs_newer", "shift_train_vs_test_repos",
                                              "filled_older", "filled_newer"])
    out["notes"] = [", ".join(n for n, flag in (("mostly says which repo", rs > S.repo_share),
                                                 ("changed over time", d1 > S.drift),
                                                 ("differs in test repos", d2 > S.drift),
                                                 ("filled in differently over time", abs(fo - fn) > 0.2)) if flag)
                    for rs, d1, d2, fo, fn in zip(out["repo_share"].fillna(0), out["shift_older_vs_newer"].fillna(0),
                                                  out["shift_train_vs_test_repos"].fillna(0), out["filled_older"].fillna(0),
                                                  out["filled_newer"].fillna(0))]
    split = dict(cut=str(cut.date()) if pd.notna(cut) else "n/a", test_repos=sorted(p.loc[test, "repo"].unique().tolist()),
                 train_mix=p.loc[~test, "y"].value_counts(normalize=True).reindex(range(3)).fillna(0).round(3).tolist(),
                 test_mix=p.loc[test, "y"].value_counts(normalize=True).reindex(range(3)).fillna(0).round(3).tolist())
    return out.sort_values("repo_share", ascending=False, na_position="last").reset_index(drop=True), split


# ===================================================================== 8. engineered features
def engineer(df: pd.DataFrame, log_columns=(), missing_flags=()) -> tuple[pd.DataFrame, list[dict]]:
    """Adds features built only from what is known when a PR opens, so training and scoring can share this
    function. Returns the frame with the new columns and a description of each. Columns it needs but cannot
    find are skipped. log_columns get a log1p_ copy and missing_flags an _is_missing copy (for linear models and
    neural nets; trees do not need either)."""
    out = df.copy()
    spec, new = [], {}

    def num(c):
        return _num(out[c]) if c in out.columns else None

    def add(name, values, meaning, sources):
        new[name] = values
        spec.append(dict(feature=name, meaning=meaning, built_from=", ".join(sources)))

    la, ld, nf = num("LA"), num("LD"), num("NF")
    if la is not None and ld is not None:
        total = la + ld
        add("total_lines", total, "lines added + deleted", ["LA", "LD"])
        with np.errstate(invalid="ignore", divide="ignore"):
            add("delete_share", np.where(total > 0, ld / total, np.nan), "share of changed lines that are deletions", ["LA", "LD"])
            if nf is not None:
                add("lines_per_file", np.where(nf > 0, total / nf, np.nan), "changed lines per changed file", ["LA", "LD", "NF"])
    merged_before = num("author_prs_merged_repo")
    if merged_before is not None:
        never = np.where(np.isfinite(merged_before), (merged_before == 0).astype(float), np.nan)
        add("author_never_merged_here", never, "author has no merged PR in the repo yet", ["author_prs_merged_repo"])
        if "ai_agent_authored" in out.columns:
            add("agent_and_new_author", np.where(np.isfinite(never), never * _true(out, "ai_agent_authored"), np.nan),
                "an AI-agent PR from an author with no merged PR in the repo yet",
                ["author_prs_merged_repo", "ai_agent_authored"])
    if "created_at" in out.columns:
        t = _times(out["created_at"])
        add("opened_hour_utc", t.dt.hour.astype("Float64").to_numpy(float, na_value=np.nan), "hour the PR was opened (UTC)", ["created_at"])
        add("opened_weekday", t.dt.weekday.astype("Float64").to_numpy(float, na_value=np.nan), "weekday opened (0 = Monday)", ["created_at"])
        add("opened_on_weekend", (t.dt.weekday >= 5).astype(float).where(t.notna()).to_numpy(float), "opened on a Saturday or Sunday", ["created_at"])
    body = num("body_len")
    if body is not None:
        add("has_description", np.where(np.isfinite(body), (body > 0).astype(float), np.nan), "the PR has a description", ["body_len"])
    so, sm = num("max_sim_open_prs"), num("max_sim_recent_merged")
    if so is not None and sm is not None:
        add("similar_open_minus_merged", so - sm, "how much more the PR resembles open PRs than recently merged ones "
            "(a duplicate hint)", ["max_sim_open_prs", "max_sim_recent_merged"])
    conc = num("concurrent_open_prs_same_files")
    if conc is not None:
        add("competing_prs_on_same_files", np.where(np.isfinite(conc), (conc > 0).astype(float), np.nan),
            "another open PR touches the same files", ["concurrent_open_prs_same_files"])
    for c in log_columns:
        x = num(c)
        if x is not None:
            add(f"log1p_{c}", np.log1p(np.clip(x, 0, None)), f"log(1 + {c}), for linear models and neural nets", [c])
    for c in missing_flags:
        if c in out.columns:
            add(f"{c}_is_missing", out[c].isna().astype(float).to_numpy(), f"{c} is missing", [c])
    out = out.drop(columns=[c for c in new if c in out.columns])
    return pd.concat([out, pd.DataFrame(new, index=out.index)], axis=1), spec


def judge_engineered(p_eng, spec, sig, S):
    """Signal of each engineered feature next to the best signal among the columns it is built from."""
    y = p_eng["y"].to_numpy()
    sigmap = sig.set_index("feature")["signal"].to_dict() if len(sig) else {}
    rows = []
    for sp in spec:
        x = _num(p_eng[sp["feature"]])
        ok = np.isfinite(x)
        best = 0.5
        if ok.sum() >= S.min_rows and np.unique(x[ok]).size > 1:
            for k in range(3):
                a = auroc(y[ok] == k, x[ok])
                if np.isfinite(a):
                    best = max(best, a, 1 - a)
        srcs = [s.strip() for s in sp["built_from"].split(",")]
        base = max([sigmap.get(s, np.nan) for s in srcs if np.isfinite(sigmap.get(s, np.nan))], default=np.nan)
        if sp["feature"].startswith("log1p_"):
            verdict = "same ranking as its source (only matters for linear models / neural nets)"
        elif not np.isfinite(base):
            verdict = f"new information ({strength(best)} signal)"
        else:
            verdict = "adds signal" if best > base + 0.01 else "no better than its source"
        rows.append(dict(feature=sp["feature"], meaning=sp["meaning"], built_from=sp["built_from"], filled=float(ok.mean()),
                         signal=best, best_source_signal=base, verdict=verdict))
    return pd.DataFrame(rows)


# ===================================================================== 9. quick model check
def _matrix(df, cols):
    data = {}
    for c in cols:
        x = _num(df[c])
        if x is None:
            vc = df[c].astype("string")
            if vc.nunique(dropna=True) <= 30:
                codes = pd.Categorical(vc).codes.astype(float)
                codes[codes < 0] = np.nan
                data[c] = codes
            continue
        data[c] = x
    return pd.DataFrame(data, index=df.index)


def quick_model(p, cols, groups, S, notes):
    """Fits a quick gradient-boosted model on the train repos and scores the test repos. Returns the test scores and
    how much the score worsens when each feature group is shuffled (the group's real contribution)."""
    lib = None
    try:
        import lightgbm as lgb  # noqa: F401
        lib = "lightgbm"
    except ImportError:
        try:
            from sklearn.ensemble import HistGradientBoostingClassifier  # noqa: F401
            lib = "scikit-learn"
        except ImportError:
            notes.append("quick model check skipped: install lightgbm (or scikit-learn) to run it")
            return None
    test = frames.is_test(p, by="repo", test_frac=0.2).to_numpy()
    if test.sum() < S.min_rows or (~test).sum() < S.min_rows:
        notes.append("quick model check skipped: too few PRs on one side of the repo split")
        return None
    tr, te = p[~test], p[test]
    if len(tr) > S.model_rows:
        tr = tr.sample(n=S.model_rows, random_state=0)
    X_tr, X_te = _matrix(tr, cols), _matrix(te, cols)
    X_te = X_te.reindex(columns=X_tr.columns)
    y_tr, y_te = tr["y"].to_numpy(), te["y"].to_numpy()

    def fit(X, yv):
        if lib == "lightgbm":
            import lightgbm as lgb
            m = lgb.LGBMClassifier(objective="multiclass", n_estimators=300, learning_rate=0.05, num_leaves=63,
                                   min_child_samples=50, subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
                                   random_state=0, verbose=-1)
        else:
            from sklearn.ensemble import HistGradientBoostingClassifier
            m = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.06, early_stopping=True, random_state=0)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            m.fit(X, yv)
        return m

    def proba(m, X):
        P = np.zeros((len(X), 3))
        P[:, m.classes_.astype(int)] = m.predict_proba(X)
        return P

    model = fit(X_tr, y_tr)
    P = proba(model, X_te)
    mix = np.bincount(y_tr, minlength=3) / len(y_tr)
    base = brier(np.tile(mix, (len(y_te), 1)), y_te)
    score = brier(P, y_te)
    res = dict(library=lib, train_prs=len(tr), test_prs=len(te), test_repos=sorted(te["repo"].unique().tolist()),
               features=X_tr.shape[1], brier=score, brier_base_rate=base, skill=1 - score / base,
               auroc={CLASSES[k]: auroc(y_te == k, P[:, k]) for k in range(3)})
    rng = np.random.default_rng(0)
    sample = rng.choice(len(X_te), size=min(len(X_te), 20_000), replace=False)
    Xs, ys = X_te.iloc[sample].copy(), y_te[sample]
    ref = brier(proba(model, Xs), ys)
    by_group = {}
    for c in X_tr.columns:
        by_group.setdefault(GROUP_NAMES.get(groups.get(c, "other"), groups.get(c, "other")), []).append(c)
    rows = []
    for gname, gcols in by_group.items():
        losses = []
        for _ in range(2):
            Xp = Xs.copy()
            perm = rng.permutation(len(Xp))
            Xp[gcols] = Xp[gcols].to_numpy()[perm]
            losses.append(brier(proba(model, Xp), ys) - ref)
        rows.append(dict(group=gname, features=len(gcols), brier_worse_when_shuffled=float(np.mean(losses))))
    res["groups"] = pd.DataFrame(rows).sort_values("brier_worse_when_shuffled", ascending=False).reset_index(drop=True)
    imp = None
    if lib == "lightgbm":
        imp = pd.DataFrame({"feature": X_tr.columns, "gain": model.booster_.feature_importance("gain")})
        imp = imp.sort_values("gain", ascending=False).reset_index(drop=True)
        imp["gain_share"] = imp["gain"] / imp["gain"].sum()
    res["feature_importance"] = imp
    return res


# ===================================================================== 10. recommendations
def recommendations(prof, sig, clusters, stab, eng, model, cov_repo, quality, pop_steps, args, S):
    rec = []
    drop = prof[prof["notes"].str.contains("empty|constant", regex=True, na=False)]["feature"].tolist()
    if drop:
        rec.append(f"Drop {len(drop)} empty or (nearly) constant features: " + ", ".join(drop[:15]) + (" ..." if len(drop) > 15 else ""))
    text = prof[prof["kind"] == "text"]["feature"].tolist()
    if text:
        rec.append("Encode or drop the text columns before modelling: " + ", ".join(text[:10]))
    sus = sig[sig["strength"] == "suspicious"]["feature"].tolist()
    if sus:
        rec.append("Check these for leaks before trusting them (they predict the outcome too well on their own): "
                   + ", ".join(sus))
    if len(clusters):
        rec.append(f"{_n(len(clusters), 'group')} of features say almost the same thing; keeping one per group loses "
                   "little (see redundant_features.csv)")
    repoish = stab[stab["repo_share"] > S.repo_share]["feature"].tolist() if len(stab) else []
    if repoish:
        rec.append("These mostly say which repo a PR is from, so they may not carry over to new repos: "
                   + ", ".join(repoish[:12]) + (" ..." if len(repoish) > 12 else ""))
    drift = stab[stab["shift_older_vs_newer"] > S.drift]["feature"].tolist() if len(stab) else []
    if drift:
        rec.append("These changed a lot between older and newer PRs; prefer a time-based check and recent training "
                   "data: " + ", ".join(drift[:12]))
    infm = sig[sig["missing_says_something"] == True]["feature"].tolist()  # noqa: E712
    if infm:
        rec.append(f"For {_n(len(infm), 'feature')}, being missing itself predicts the outcome: keep the blanks for "
                   "trees, add _is_missing flags for linear models and neural nets")
    if eng is not None and len(eng):
        good = eng[eng["verdict"] == "adds signal"]["feature"].tolist()
        if good:
            rec.append("Engineered features worth trying: " + ", ".join(good))
    if model is not None:
        g = model["groups"]
        top = g.head(3)["group"].tolist()
        rec.append(f"Quick model: skill {model['skill']:.3f} over the base rate on unseen repos; the groups it leans on "
                   f"most are {', '.join(top)}")
        low = g[g["brier_worse_when_shuffled"] <= 0.0005]["group"].tolist()
        if low:
            rec.append("These groups barely matter to the quick model (candidates to drop, which also cuts mining or "
                       "scoring cost): " + ", ".join(low))
        llm = g[g["group"] == GROUP_NAMES["llm_flags"]]
        if len(llm):
            rec.append(f"LLM flags add {llm['brier_worse_when_shuffled'].iloc[0]:.4f} Brier; weigh that against needing "
                       "an LLM call for every PR at scoring time")
    if cov_repo is not None and len(cov_repo) and cov_repo["with_snapshots"].mean() < 0.9:
        rec.append(f"Only {cov_repo['with_snapshots'].mean():.0%} of PRs (average over repos) have snapshots, and "
                   "frames.py trains the outcome model on snapshots: find out why before training (see section 2)")
    if quality.get("abandoned_closed_within_1_hour", 0) > 0.1:
        rec.append(f"{quality['abandoned_closed_within_1_hour']:.0%} of 'abandoned' PRs closed within an hour: those are "
                   "withdrawals or silent rejections, not abandonment; fix the label or treat them separately")
    maint = dict(pop_steps.values.tolist()).get("  of which the author had already merged or closed others' PRs", 0)
    total = pop_steps["PRs"].iloc[-1]
    if not args.strict_outside and total and maint / max(total, 1) > 0.1:
        rec.append(f"{maint / total:.0%} of the 'outside' PRs are by authors who had already merged or closed others' "
                   "PRs; consider --strict-outside (author_confirmed_maintainer) for training")
    if quality.get("prs_without_llm_flags", 0) > 0.2:
        rec.append(f"{quality['prs_without_llm_flags']:.0%} of the analysis PRs have no LLM flags; a model will rely on "
                   "them less, and missing flags may mean skipped or failed calls")
    return rec


# ===================================================================== charts
PALETTE = {"lands_clean": "#2a9d8f", "lands_after_rework": "#e9c46a", "goes_nowhere": "#7b5ea7"}


def make_charts(out_dir, ctx):
    os.makedirs(out_dir, exist_ok=True)
    made = []

    def save(fig, name):
        path = os.path.join(out_dir, name)
        fig.tight_layout()
        fig.savefig(path, dpi=120)
        plt.close(fig)
        made.append(path)

    def outcome_by_repo():
        r = ctx["repo"].dropna(subset=list(CLASSES)).sort_values("analysis_prs")
        fig, ax = plt.subplots(figsize=(10, 0.32 * len(r) + 1.5))
        left = np.zeros(len(r))
        for c in CLASSES:
            ax.barh(r["repo"], r[c], left=left, color=PALETTE[c], label=c)
            left += r[c].to_numpy()
        for i, n in enumerate(r["analysis_prs"]):
            ax.text(1.01, i, f"{int(n):,}", va="center", fontsize=7)
        ax.set_xlim(0, 1.12)
        ax.set_xlabel("share of the repo's analysis PRs (number of PRs on the right)")
        ax.set_title("What happened to outside PRs, per repo")
        ax.legend(fontsize=8, loc="lower right")
        save(fig, "01_outcome_by_repo.png")

    def outcome_by_month():
        p = ctx["p"]
        m = pd.DataFrame({"month": p["_created"].dt.tz_convert(None).dt.to_period("M").astype(str), "y": p["y"]})
        t = m.groupby("month")["y"].value_counts(normalize=True).unstack().reindex(columns=range(3)).fillna(0)
        n = m.groupby("month").size()
        keep = n[n >= 100].index
        t = t.loc[keep]
        if len(t) < 2:
            return
        fig, (a1, a2) = plt.subplots(2, 1, figsize=(12, 6.5), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
        for k, c in enumerate(CLASSES):
            a1.plot(range(len(t)), t[k], color=PALETTE[c], label=c)
        a1.set_ylabel("share of PRs")
        a1.set_title("Outcome mix by month opened (months with 100+ PRs)")
        a1.legend(fontsize=8)
        a2.bar(range(len(t)), n.loc[keep].to_numpy(), color="#999999")
        a2.set_ylabel("PRs")
        step = max(1, len(t) // 20)
        a2.set_xticks(range(0, len(t), step))
        a2.set_xticklabels(list(t.index)[::step], rotation=60, fontsize=7)
        save(fig, "02_outcome_by_month.png")

    def snapshot_coverage():
        c = ctx["cov_repo"]
        if c is None or not len(c):
            return
        c = c.sort_values("prs")
        fig, ax = plt.subplots(figsize=(10, 0.32 * len(c) + 1.5))
        yy = np.arange(len(c))
        ax.barh(yy - 0.2, c["with_snapshots"], 0.4, color="#1f77b4", label="has snapshots")
        ax.barh(yy + 0.2, c["with_commit_features"], 0.4, color="#ff7f0e", label="has commit features")
        ax.set_yticks(yy)
        ax.set_yticklabels(c["repo"], fontsize=8)
        ax.set_xlim(0, 1)
        ax.set_xlabel("share of the repo's PRs")
        ax.set_title("Which PRs have snapshots and commit features")
        ax.legend(fontsize=8)
        save(fig, "03_snapshot_coverage.png")

    def fill_rates():
        pr = ctx["prof"]
        g = pr.groupby("group")["filled"].agg(["mean", "size"]).sort_values("mean")
        fig, ax = plt.subplots(figsize=(10, 0.4 * len(g) + 1.5))
        ax.barh([f"{i} ({int(n)})" for i, n in zip(g.index, g["size"])], g["mean"], color="#4c78a8")
        ax.set_xlim(0, 1)
        ax.set_xlabel("average share of PRs with the feature filled in (number of features in brackets)")
        ax.set_title("How complete each feature group is")
        save(fig, "04_feature_fill_by_group.png")

    def top_signal():
        s = ctx["sig"].dropna(subset=["signal"]).head(30)[::-1]
        if not len(s):
            return
        colors = ["#d62728" if st == "suspicious" else "#1f77b4" for st in s["strength"]]
        fig, ax = plt.subplots(figsize=(10, 0.3 * len(s) + 1.5))
        ax.barh(s["feature"], s["signal"], color=colors)
        ax.axvline(Settings.suspicious, color="k", ls="--", lw=1, label=f"suspicious above {Settings.suspicious}")
        ax.set_xlim(0.5, 1)
        ax.set_xlabel("signal on its own (AUROC for the outcome it predicts best; 0.5 = none)")
        ax.set_title("The 30 most telling features")
        ax.legend(fontsize=8)
        save(fig, "05_top_feature_signal.png")

    def binned():
        tops = [f for f in ctx["sig"].dropna(subset=["signal"])["feature"] if f in ctx["p"].columns][:12]
        tabs = [(f, binned_rates(ctx["p"], f)) for f in tops]
        tabs = [(f, t) for f, t in tabs if t is not None]
        if not tabs:
            return
        ncol = 4
        nrow = math.ceil(len(tabs) / ncol)
        fig, axes = plt.subplots(nrow, ncol, figsize=(4.2 * ncol, 3.2 * nrow), squeeze=False)
        for ax, (f, t) in zip(axes.flat, tabs):
            for c, col in zip(CLASSES, ("clean", "rework", "nowhere")):
                ax.plot(range(len(t)), t[col], "o-", ms=3, color=PALETTE[c], label=c)
            ax.set_title(f, fontsize=9)
            ax.set_xticks(range(len(t)))
            ax.set_xticklabels([f"{v:.3g}" for v in t["x"]], rotation=60, fontsize=6)
            ax.set_ylim(0, 1)
        for ax in list(axes.flat)[len(tabs):]:
            ax.axis("off")
        axes.flat[0].legend(fontsize=7)
        fig.suptitle("Outcome share across each feature's range (PRs split into 10 equal groups; median value below)")
        save(fig, "06_top_features_by_value.png")

    def heatmap():
        cols = [f for f in ctx["sig"].dropna(subset=["signal"])["feature"] if f in ctx["corr_cols"]][:30]
        if len(cols) < 3:
            return
        sample = ctx["p"].sample(n=min(len(ctx["p"]), Settings.corr_rows), random_state=0)
        corr = pd.DataFrame({c: _num(sample[c]) for c in cols}).rank().corr(min_periods=300)
        fig, ax = plt.subplots(figsize=(11, 9.5))
        im = ax.imshow(corr.to_numpy(), cmap="RdBu_r", vmin=-1, vmax=1)
        ax.set_xticks(range(len(cols)))
        ax.set_xticklabels(cols, rotation=90, fontsize=7)
        ax.set_yticks(range(len(cols)))
        ax.set_yticklabels(cols, fontsize=7)
        fig.colorbar(im, ax=ax, shrink=0.7, label="rank correlation")
        ax.set_title("How the 30 most telling features move together (dark = they say the same thing)")
        save(fig, "07_feature_overlap.png")

    def repo_vs_signal():
        st, sg = ctx["stab"], ctx["sig"]
        if not len(st):
            return
        m = st.merge(sg[["feature", "signal"]], on="feature").dropna(subset=["repo_share", "signal"])
        fig, ax = plt.subplots(figsize=(9, 6))
        ax.scatter(m["repo_share"], m["signal"], s=14, alpha=0.6, color="#1f77b4")
        for _, r in m.sort_values("signal", ascending=False).head(12).iterrows():
            ax.annotate(r["feature"], (r["repo_share"], r["signal"]), fontsize=6)
        ax.axvline(Settings.repo_share, color="k", ls="--", lw=1)
        ax.set_xlabel("how much of the feature's spread is just which repo it is (0 to 1)")
        ax.set_ylabel("signal on its own (AUROC)")
        ax.set_title("Telling features that mostly identify the repo (right side) may not carry over to new repos")
        save(fig, "08_repo_share_vs_signal.png")

    def drift():
        st = ctx["stab"].dropna(subset=["shift_older_vs_newer"])
        if not len(st):
            return
        st = st.sort_values("shift_older_vs_newer", ascending=False).head(25)[::-1]
        fig, ax = plt.subplots(figsize=(10, 0.3 * len(st) + 1.5))
        ax.barh(st["feature"], st["shift_older_vs_newer"], color="#e45756")
        ax.axvline(Settings.drift, color="k", ls="--", lw=1, label=f"big shift above {Settings.drift}")
        ax.set_xlabel(f"shift between PRs opened before and after {ctx['split']['cut']} (population stability index)")
        ax.set_title("Features that changed most over time")
        ax.legend(fontsize=8)
        save(fig, "09_feature_drift.png")

    def groups():
        mdl = ctx["model"]
        if mdl is None:
            return
        g = mdl["groups"][::-1]
        fig, ax = plt.subplots(figsize=(10, 0.4 * len(g) + 1.5))
        ax.barh([f"{a} ({b})" for a, b in zip(g["group"], g["features"])], g["brier_worse_when_shuffled"], color="#54a24b")
        ax.axvline(0, color="k", lw=1)
        ax.set_xlabel("how much worse the quick model's Brier gets when the group is shuffled (higher = more needed)")
        ax.set_title(f"Which feature groups the quick model needs (skill {mdl['skill']:.3f} on unseen repos)")
        save(fig, "10_feature_group_importance.png")

    def engineered():
        e = ctx["eng"]
        if e is None or not len(e):
            return
        e = e[~e["feature"].str.startswith(("log1p_", ))]
        e = e[~e["feature"].str.endswith("_is_missing")]
        if not len(e):
            return
        e = e.iloc[::-1]
        yy = np.arange(len(e))
        fig, ax = plt.subplots(figsize=(10, 0.4 * len(e) + 1.5))
        ax.barh(yy + 0.2, e["signal"], 0.4, color="#1f77b4", label="engineered feature")
        ax.barh(yy - 0.2, e["best_source_signal"].fillna(0.5), 0.4, color="#bbbbbb", label="best of the columns it uses")
        ax.set_yticks(yy)
        ax.set_yticklabels(e["feature"], fontsize=8)
        ax.set_xlim(0.5, max(0.75, float(np.nanmax(e["signal"])) + 0.02))
        ax.set_xlabel("signal on its own (AUROC)")
        ax.set_title("Engineered features vs the columns they are built from")
        ax.legend(fontsize=8)
        save(fig, "11_engineered_features.png")

    for fn in (outcome_by_repo, outcome_by_month, snapshot_coverage, fill_rates, top_signal, binned, heatmap,
               repo_vs_signal, drift, groups, engineered):
        try:
            fn()
        except Exception as e:      # one broken chart must not stop the rest
            print(f"  chart skipped ({fn.__name__}): {type(e).__name__}: {e}")
    return made


# ===================================================================== run
def run(args, S):
    notes = []
    t = frames.load_tables(args.data, check_fresh=not args.no_fresh_check)
    d = t.dataset
    check, manifest, same_repos = data_check(args.data, t)
    p, steps = build_population(d, args, S, notes)
    if len(p) < S.min_rows:
        raise SystemExit(f"only {len(p)} analysis PRs: too few to analyse")
    non_inputs = frames.non_input_columns()
    cols = [c for c in d.columns if c not in non_inputs and not c.startswith(("label_", "days_to_"))]
    groups = feature_groups(cols)
    sample = p.sample(n=S.max_rows, random_state=0) if len(p) > S.max_rows else p

    _section("0. DATA CHECK")
    if manifest:
        shards = ", ".join(f"{k} ({v.get('miner_version', '?')})" for k, v in manifest.get("shards", {}).items())
        print(f"Build {manifest.get('built_at', '?')}, split id {manifest.get('split_id', '?')}, shards: {shards}")
    print(_table(check))
    print("checksum OK = the file is exactly the one MANIFEST.json describes. Same repos in every table: "
          + ("yes" if same_repos else "NO"))

    _section("1. WHAT IS IN THE DATA")
    year_tab, repo_tab, created = overview(d, p)
    print(f"{_n(len(d), 'PR')} in {_n(d['repo'].nunique(), 'repo')}, opened {created.min():%Y-%m-%d} to {created.max():%Y-%m-%d}.")
    print("\nWhich PRs this report analyses (the plan's training rows):")
    print(_table(steps))
    print("\nPer year (outcome shares are for the analysis PRs):")
    print(_table(year_tab))
    print(f"\nPer repo (plan: at most {PLAN['cap']:,} PRs, at most half bot PRs, {PLAN['min_unmerged']:.0%}-"
          f"{PLAN['max_unmerged']:.0%} going nowhere):")
    print(_table(repo_tab))
    before = float((created < pd.Timestamp(PLAN["since"], tz="UTC")).mean())
    print(f"\n{before:.1%} of all PRs were opened before {PLAN['since']}, the start of the plan's window"
          + (" (use --since to analyse the window only)." if before > 0.05 and not args.since else "."))

    _section("2. SNAPSHOTS AND COMMIT FEATURES")
    cov_repo, cov_cuts, _ = coverage(d, t)
    print(_table(cov_repo))
    print("\nBy kind of PR (share with snapshots / with commit features):")
    print(_table(cov_cuts))
    ss = snapshot_summary(t)
    if ss:
        summ, trig = ss
        print("\n" + ", ".join(f"{k}: {v:,.3f}" if isinstance(v, float) else f"{k}: {v:,}" for k, v in summ.items()))
        if trig is not None:
            print(_table(trig))
    print("frames.py trains the outcome model on snapshot rows, so PRs without snapshots are not in its training data. "
          "If coverage is high only from some date onwards (first_pr_with_snapshots), the snapshots were captured only "
          "for newer PRs.")

    _section("3. LABEL QUALITY")
    quality = label_quality(d, p)
    for k, v in quality.items():
        if k == "after_merge":
            continue
        print(f"  {k}: " + (_pct(v) if isinstance(v, float) and v <= 1 and "hours" not in k else
                            f"{v:.1f}" if isinstance(v, float) else str(v)))
    if len(quality.get("after_merge", [])):
        print("\nAfter-merge labels (on merged PRs; observed = the label could really be checked):")
        print(_table(quality["after_merge"]))

    _section("4. WHAT THE FEATURES LOOK LIKE")
    prof = profile(sample, cols, groups, S)
    print(f"{_n(len(cols), 'possible input')} (frames.py's rules: no ids, outcomes, labels or bookkeeping).")
    gsum = prof.groupby("group").agg(features=("feature", "size"), average_filled=("filled", "mean"),
                                     empty_or_constant=("notes", lambda n: int(n.str.contains("empty|constant", regex=True).sum())))
    print(_table(gsum.reset_index().sort_values("features", ascending=False)))
    flagged = prof[(prof["notes"] != "") & (prof["notes"] != "long tail")]
    tails = int((prof["notes"] == "long tail").sum())
    print(f"\nFeatures with something to note ({len(flagged)}; another {tails} only have a long tail, see "
          "feature_profile.csv):")
    print(_table(flagged[["feature", "group", "kind", "filled", "distinct", "top_value_share", "skew", "notes"]]))

    _section("5. HOW MUCH EACH FEATURE TELLS US")
    sig = signal(sample, cols, groups, S)
    print("signal: AUROC of the feature on its own for the outcome it predicts best (0.5 = nothing, 0.6 = some, "
          f"0.7 = a lot, above {S.suspicious} = suspicious, usually a leak).")
    print(_table(sig.head(40)[["feature", "group", "signal", "strength", "direction", "auroc_clean", "auroc_rework", "auroc_nowhere"]]))
    best = sig.dropna(subset=["signal"]).groupby("group").head(1)[["group", "feature", "signal"]]
    print("\nBest feature in each group:")
    print(_table(best))
    infm = sig[sig["missing_says_something"] == True]  # noqa: E712
    if len(infm):
        print("\nFeatures whose blanks predict the outcome (goes-nowhere rate when missing vs filled):")
        print(_table(infm[["feature", "group", "nowhere_if_missing", "nowhere_if_filled"]]))
    cat = sig.dropna(subset=["nowhere_rate_spread"])
    if len(cat):
        print("\nText features with a few values (spread of the goes-nowhere rate between values):")
        print(_table(cat[["feature", "nowhere_rate_spread", "direction"]]))

    _section("6. FEATURES THAT REPEAT EACH OTHER")
    pairs, clusters, corr_cols = redundancy(sample, cols, sig, S)
    print(f"Pairs whose values move together almost perfectly (rank correlation {S.redundant}+): {len(pairs)}")
    print(_table(pairs.head(30)))
    if len(clusters):
        print("\nGroups of near-copies, keeping the most telling one:")
        print(_table(clusters))

    _section("7. FEATURES THAT CHANGE BY REPO OR OVER TIME")
    stab, split = stability(sample, cols, S)
    print(f"repo_share: how much of a feature's spread is just which repo it is (above {S.repo_share} = it mostly "
          "identifies the repo). shift: population stability index between PRs opened before and after "
          f"{split['cut']}, and between train and test repos (above {S.drift} = a big shift).")
    print(f"Test repos in frames.py's repo split: {', '.join(split['test_repos'])}")
    print(f"Outcome mix (clean, rework, nowhere): train repos {split['train_mix']}, test repos {split['test_mix']}")
    print(_table(stab[stab["notes"] != ""].head(40)))

    _section("8. ENGINEERED FEATURES")
    heavy = prof[prof["notes"].str.contains("long tail", na=False)]["feature"].tolist()
    infm_cols = infm["feature"].tolist() if len(infm) else []
    p_eng, spec = engineer(sample, log_columns=heavy, missing_flags=infm_cols)
    eng = judge_engineered(p_eng, spec, sig, S)
    print("Built by engineer() in this file, from at-open columns only, so the model and the scorer can share it.")
    print(_table(eng[~eng["feature"].str.startswith("log1p_") & ~eng["feature"].str.endswith("_is_missing")]))
    print(f"\nAlso built: log1p_ copies of {_n(len(heavy), 'long-tailed feature')} and _is_missing flags for "
          f"{_n(len(infm_cols), 'feature')} whose blanks carry signal. Trees do not need them; linear models and "
          "neural nets do.")

    _section("9. QUICK MODEL CHECK")
    eng_cols = [sp["feature"] for sp in spec if not sp["feature"].startswith("log1p_") and not sp["feature"].endswith("_is_missing")]
    model = None
    left_out = sig.loc[sig["strength"] == "suspicious", "feature"].tolist()
    if not args.no_model:
        p_all, _ = engineer(p, missing_flags=())
        model = quick_model(p_all, [c for c in cols + eng_cols if c not in left_out],
                            {**groups, **{c: "engineered" for c in eng_cols}}, S, notes)
    if model is None:
        why = "--no-model" if args.no_model else notes[-1].replace("quick model check skipped: ", "")
        print(f"(skipped: {why})")
    else:
        if left_out:
            print("Left out because they look like leaks (section 5): " + ", ".join(left_out))
        print(f"{model['library']}, {_n(model['features'], 'feature')}, trained on {model['train_prs']:,} PRs from the train "
              f"repos, scored on {model['test_prs']:,} PRs from {', '.join(model['test_repos'])}.")
        print(f"Brier {model['brier']:.4f} vs base rate {model['brier_base_rate']:.4f}: skill {model['skill']:.3f}. AUROC: "
              + ", ".join(f"{k} {v:.3f}" for k, v in model["auroc"].items()))
        print("\nHow much each feature group matters (Brier gets worse by this much when the group is shuffled):")
        print(_table(model["groups"], 4))
        if model["feature_importance"] is not None:
            print("\nTop 20 single features (share of the model's gain):")
            print(_table(model["feature_importance"].head(20), 4))
        print("This is a quick check with default settings, not the final model.")

    _section("10. RECOMMENDATIONS")
    recs = recommendations(prof, sig, clusters, stab, eng, model, cov_repo, quality, steps, args, S)
    for i, r in enumerate(recs, 1):
        print(f"{i}. {r}")
    for n in notes:
        print("  note: " + n)

    ctx = dict(p=p, repo=repo_tab, cov_repo=cov_repo, prof=prof, sig=sig, corr_cols=corr_cols, stab=stab, split=split,
               model=model, eng=eng)
    write_outputs(args, ctx, check, steps, year_tab, cov_cuts, quality, pairs, clusters, recs, cols, prof, sig)
    if args.save_features:
        full, _ = engineer(p, log_columns=heavy, missing_flags=infm_cols)
        keep = KEY_COLS + ["created_at", "outcome_3class", "nowhere_reason"] + cols + [sp["feature"] for sp in spec]
        path = os.path.join(args.out, "features_at_open.parquet")
        full[[c for c in dict.fromkeys(keep) if c in full.columns]].to_parquet(path, index=False)
        print(f"  saved the at-open rows with engineered features: {path}")


KEY_COLS = ["repo", "pr_number"]


def write_outputs(args, ctx, check, steps, year_tab, cov_cuts, quality, pairs, clusters, recs, cols, prof, sig):
    out = args.out
    tables = dict(data_check=check, analysis_rows=steps, by_year=year_tab, by_repo=ctx["repo"],
                  snapshot_coverage_by_repo=ctx["cov_repo"], snapshot_coverage_by_kind=cov_cuts,
                  after_merge_labels=quality.get("after_merge"), feature_profile=prof, feature_signal=sig,
                  redundant_pairs=pairs, redundant_features=clusters, feature_stability=ctx["stab"],
                  engineered_features=ctx["eng"])
    if ctx["model"] is not None:
        tables["model_group_importance"] = ctx["model"]["groups"]
        tables["model_feature_importance"] = ctx["model"]["feature_importance"]
    for name, tab in tables.items():
        if tab is not None and len(tab):
            tab.to_csv(os.path.join(out, f"{name}.csv"), index=False)
    drop = set(prof[prof["notes"].str.contains("empty|constant", regex=True, na=False)]["feature"])
    sus = set(sig[sig["strength"] == "suspicious"]["feature"])
    with open(os.path.join(out, "features_recommended.txt"), "w", encoding="utf-8") as fh:
        fh.write("# Inputs that passed explore_features.py's checks: not empty or constant.\n")
        fh.write("# Lines starting with '# check:' predict the outcome suspiciously well on their own; look before using.\n")
        for c in cols:
            if c in drop:
                continue
            fh.write(("# check: " if c in sus else "") + c + "\n")
    with open(os.path.join(out, "recommendations.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(f"{i}. {r}" for i, r in enumerate(recs, 1)) + "\n")
    made = make_charts(os.path.join(out, "charts"), ctx)
    _section("FILES")
    print(f"Everything is in {os.path.abspath(out)} (report.txt is this text)")
    for p in made:
        print("  " + p)


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], epilog=__doc__.split("\n\n", 1)[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=os.path.join(HERE, "data", "dataset_merged.parquet"),
                    help="the dataset table; its _snapshots and _commit_features files are found next to it")
    ap.add_argument("--out", default=os.path.join(REPO_ROOT, "data", "feature_report"),
                    help="folder for the report, tables and charts (default data/feature_report, which is gitignored)")
    ap.add_argument("--strict-outside", action="store_true",
                    help="also drop PRs whose author had already merged or closed others' PRs")
    ap.add_argument("--since", help="only PRs opened on or after this date, e.g. 2023-07-01")
    ap.add_argument("--no-model", action="store_true", help="skip the quick model check")
    ap.add_argument("--save-features", action="store_true",
                    help="also save the at-open rows with the engineered features (features_at_open.parquet)")
    ap.add_argument("--no-fresh-check", action="store_true", help="skip frames.py's snapshot freshness check")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    args.out = os.path.normpath(args.out)
    os.makedirs(args.out, exist_ok=True)
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    tee = _Tee(os.path.join(args.out, "report.txt"), sys.stdout)
    sys.stdout = tee
    try:
        run(args, Settings())
    finally:
        sys.stdout = tee.stream
        tee.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
