#!/usr/bin/env python
"""
compare_runs.py - judge two or more models' prediction files on exactly the same PRs with eval_script.py, and write
one side-by-side report.

    python pipelines/evaluation/compare_runs.py --run nishit=A.csv --run ananth=B.csv --run preeyal=C.csv --out DIR
    python pipelines/evaluation/compare_runs.py --a A.csv --a-name nishit --b B.csv --b-name ananth --out DIR

Same PRs  only PRs in every file are kept (matched on repo, lower-cased, and pr_number); a PR whose outcome_3class
          differs between the files is dropped and counted.
Same truth every column that is not a prediction (labels, reasons, observation flags, dates, grouping columns) is
          taken from one run (--truth-from, default the first) for all, so the evaluations differ only in the p_*
          columns. A truth column that run lacks is taken from the first other run that has it. Where several
          files have a column, the report says how often each agrees with the truth run, so a different label
          definition shows up.
Same rules every file goes through the same eval_script.py with the same settings. Without --train, its base
          rate comes from the test PRs themselves, so it is the same for all; with --train, the same file is used.
Paired    on the PRs eval_script.py kept (it drops PRs without an outcome and PRs too young to have settled), the
          differences between every pair of runs in Brier (repo-averaged, as eval_script.py reports it), log loss
          and AUROC per outcome, with 95% ranges from resampling whole repos (the same draws for every pair).
          Seeds are averaged per PR first.

Writes to --out:
  inputs/<name>.csv                each file as evaluated
  eval_<name>/                     eval_script.py's full output for each
  comparison.html                  the report: every metric, check and chart side by side (self-contained)
  metrics_side_by_side.csv         every number in the report's tables, with the best run
  paired.csv, per_repo.csv         the paired differences and the per-repo table
  comparison.md                    the headline tables and notes, for pasting

Needs numpy, pandas and matplotlib (for eval_script.py's charts).
"""
from __future__ import annotations

import argparse
import base64
import html
import importlib.util
import itertools
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
EVAL_SCRIPT = os.path.join(HERE, "eval_script.py")
CLASSES = ("lands_clean", "lands_after_rework", "goes_nowhere")
PCOLS = ("p_clean", "p_rework", "p_nowhere")
COLOURS = ("#2b6cb0", "#b4532a", "#2f7d4f", "#7b4bb3", "#a0761c", "#b8326e")       # light theme
COLOURS_DARK = ("#7fb0e8", "#e59a74", "#7cc59a", "#b99be6", "#e0bc68", "#ee8fb7")  # dark theme
LOWER = ("brier", "rps", "log_loss", "calibration_error", "outcome_and_reason_brier")   # prefixes: lower is better
HIGHER = ("skill", "rps_skill", "auroc", "auprc", "macro_f1", "balanced_accuracy", "accuracy", "p_at_", "r_at_",
          "ndcg_", "effort_score", "f1_average")
HEADLINE = [
    ("brier", "Brier, repo-averaged"), ("skill", "Skill vs base rate"), ("log_loss", "Log loss"), ("rps", "RPS"),
    ("calibration_error", "Calibration error"), ("slope_clean", "Calibration slope, clean"),
    ("slope_rework", "Calibration slope, rework"), ("slope_nowhere", "Calibration slope, nowhere"),
    ("auroc_clean", "AUROC clean"), ("auroc_rework", "AUROC rework"), ("auroc_nowhere", "AUROC goes nowhere"),
    ("auroc_average", "AUROC average"), ("auprc_nowhere", "AUPRC goes nowhere"), ("macro_f1", "Macro F1"),
    ("balanced_accuracy", "Balanced accuracy"), ("accuracy", "Accuracy"),
]
QUEUE = [("p_at_5", "Review next: top-5 hit rate"), ("p_at_10", "Review next: top-10 hit rate"),
         ("ndcg_at_10", "Review next: top-10 quality"), ("effort_score", "Effort-aware score"),
         ("p_at_5_new", "Nowhere list: top-5, new PRs"), ("ndcg_at_10_new", "Nowhere list: top-10 quality, new PRs")]
WHY = [("f1_average", "Reason F1, averaged"), ("f1_average_unknown_counts_as_wrong", "Reason F1, unknown counts as wrong"),
       ("outcome_and_reason_brier", "Outcome and reason Brier"), ("model_says_unknown", "Model says unknown"),
       ("really_unknown", "Really unknown")]


# ===================================================================== helpers
def _key(df):
    num = pd.to_numeric(df["pr_number"], errors="coerce").round().astype("Int64").astype(str)
    return df["repo"].astype(str).str.strip().str.lower() + "#" + num


def _is_pred(c):
    return c.startswith("p_") or c in ("latency_ms", "seed")


def slug(name):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or "run"


def direction(metric):
    """'lower', 'higher', 'one' (closest to 1 wins) or None (no best run)."""
    m = metric.split(":")[-1]
    if m.startswith("slope"):
        return "one"
    if m.startswith(("model_says_unknown", "really_unknown", "offset", "prs", "share_yes", "base_rate")):
        return None
    if m.startswith(LOWER) and not m.startswith("brier_skill"):
        return "lower"
    if m.startswith(HIGHER):
        return "higher"
    return None


def best_of(metric, values, tol=1e-12):
    """Index of the best value, or None when there is no direction, fewer than two values, or a tie at the top."""
    d = direction(metric)
    vals = [(i, v) for i, v in enumerate(values) if v is not None and np.isfinite(v)]
    if d is None or len(vals) < 2:
        return None
    key = (lambda v: abs(v - 1)) if d == "one" else (lambda v: v) if d == "lower" else (lambda v: -v)
    ranked = sorted(vals, key=lambda t: key(t[1]))
    if abs(key(ranked[0][1]) - key(ranked[1][1])) <= tol:
        return None
    return ranked[0][0]


def _num(v):
    try:
        v = float(v)
        return v if np.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def auroc(y, s):
    y = np.asarray(y, bool)
    n1, n0 = float(y.sum()), float((~y).sum())        # floats: numpy sums bools to int32 on Windows, which overflows
    if n1 == 0 or n0 == 0:
        return np.nan
    r = pd.Series(s).rank(method="average").to_numpy()
    return float((r[y].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def load_eval_module():
    spec = importlib.util.spec_from_file_location("riffle_eval_script", EVAL_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod                     # dataclasses look the module up while it loads
    spec.loader.exec_module(mod)
    return mod


# ===================================================================== the inputs
def align(frames, truth_from):
    """Every file restricted to the PRs in all of them, with one shared set of truth columns."""
    names = list(frames)
    notes = []
    firsts = {}
    for n, d in frames.items():
        miss = [c for c in ("repo", "pr_number", "outcome_3class", *PCOLS) if c not in d.columns]
        if miss:
            raise SystemExit(f"run {n} is missing: {', '.join(miss)}")
        d["_key"] = _key(d)
        firsts[n] = d.drop_duplicates("_key").set_index("_key")
    common = firsts[names[0]].index
    for n in names[1:]:
        common = common.intersection(firsts[n].index)
    ref = firsts[truth_from]
    differ = pd.Index([])
    for n in names:
        if n != truth_from:
            o = firsts[n]
            bad = common[ref.loc[common, "outcome_3class"].astype(str).to_numpy()
                         != o.loc[common, "outcome_3class"].astype(str).to_numpy()]
            differ = differ.union(bad)
    keep = common.difference(differ)
    if len(keep) == 0:
        raise SystemExit("the files have no PRs in common")
    stats = dict(prs={n: len(firsts[n]) for n in names}, common=len(common), outcome_differs=len(differ),
                 evaluated_input=len(keep))
    left_out = ", ".join(f"{len(firsts[n]) - len(common):,} of {n}'s PRs are missing from another file" for n in names
                         if len(firsts[n]) - len(common))
    notes.append(f"{len(keep):,} PRs in every file are compared" + (f"; left out: {left_out}" if left_out else "")
                 + (f"; {len(differ):,} PRs whose outcome differs between the files are left out" if len(differ)
                    else "") + ".")

    order = [truth_from] + [n for n in names if n != truth_from]
    t_cols = []
    for n in order:
        t_cols += [c for c in firsts[n].columns if not _is_pred(c) and c not in t_cols]
    agree = []
    for c in t_cols:
        if c in ("repo", "pr_number") or c not in ref.columns:
            continue
        x = ref.loc[keep, c].astype(object).where(ref.loc[keep, c].notna(), "<blank>").astype(str)
        xn = pd.to_numeric(x, errors="coerce")
        for n in names:
            if n == truth_from or c not in firsts[n].columns:
                continue
            y = firsts[n].loc[keep, c].astype(object).where(firsts[n].loc[keep, c].notna(), "<blank>").astype(str)
            yn = pd.to_numeric(y, errors="coerce")
            same = (x == y) | (xn.notna() & yn.notna() & np.isclose(xn.fillna(0), yn.fillna(0)))
            agree.append(dict(column=c, run=n, agreement=float(same.mean()), differing_prs=int((~same).sum())))
    truth = pd.DataFrame(index=keep)
    source = {}
    for c in t_cols:
        src = next(n for n in order if c in firsts[n].columns)
        truth[c] = firsts[src].loc[keep, c]
        source[c] = src

    def build(d):
        p = [c for c in d.columns if _is_pred(c)]
        out = d.loc[d["_key"].isin(keep), ["_key"] + p].merge(truth, left_on="_key", right_index=True, how="left")
        front = ["repo", "pr_number", "outcome_3class"]
        return out[front + [c for c in out.columns if c not in front and c != "_key"]]
    return {n: build(frames[n]) for n in names}, stats, pd.DataFrame(agree), source, notes


def run_eval(csv, out, name, args):
    cmd = [sys.executable, EVAL_SCRIPT, "--csv", csv, "--out", out, "--model", name, "--boot", str(args.boot)]
    if args.train:
        cmd += ["--train", args.train]
    if args.features.get(name):
        cmd += ["--features-used", args.features[name]]
    if args.all_authors:
        cmd.append("--all-authors")
    print(f"  eval_script.py on {name} ...", flush=True)
    with open(out + ".log", "w", encoding="utf-8") as log:
        r = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        raise SystemExit(f"eval_script.py failed on {name}; see {out}.log")
    return json.load(open(os.path.join(out, "summary.json"), encoding="utf-8"))


# ===================================================================== paired differences
def paired(paths, boot, seed, all_authors=False):
    """Every pair of runs, on the PRs eval_script.py keeps, with 95% ranges from resampling repos."""
    names = list(paths)
    ev = load_eval_module()
    kept = ev.load_csv(argparse.Namespace(csv=paths[names[0]], all_authors=all_authors), ev.Settings(), [])["df"]
    keys = pd.Index(sorted(set(kept["_key"])))

    def avg(path):
        d = pd.read_csv(path, low_memory=False)
        d["_key"] = _key(d)
        d = d[d["_key"].isin(keys)]
        g = d.groupby("_key").agg(repo=("repo", "first"), y=("outcome_3class", "first"),
                                  **{c: (c, "mean") for c in PCOLS}).reindex(keys)
        P = g[list(PCOLS)].to_numpy(float)
        return g, P / P.sum(axis=1, keepdims=True)

    G, P = {}, {}
    for n in names:
        G[n], P[n] = avg(paths[n])
    g0 = G[names[0]]
    y = g0["y"].map({c: i for i, c in enumerate(CLASSES)}).to_numpy()
    Y = np.eye(3)[y]
    per = {n: dict(brier=((P[n] - Y) ** 2).sum(1), log_loss=-np.log(np.clip(P[n][np.arange(len(y)), y], 1e-15, 1)))
           for n in names}
    repos = g0["repo"].astype(str).str.lower().to_numpy()
    ur, codes = np.unique(repos, return_inverse=True)
    R = len(ur)
    cnt = np.bincount(codes, minlength=R).astype(float)
    repo_mean = {n: {m: np.bincount(codes, v, R) / np.maximum(cnt, 1) for m, v in per[n].items()} for n in names}

    def stats(draw):
        w = np.bincount(draw, minlength=R).astype(float)        # how many times each repo was drawn
        rw = w[codes].astype(int)
        sel = rw > 0
        out = {}
        for n in names:
            o = {m: float(np.sum(w * repo_mean[n][m]) / w.sum()) for m in ("brier", "log_loss")}
            for k, c in enumerate(("clean", "rework", "nowhere")):
                o[f"auroc_{c}"] = auroc(np.repeat(y[sel] == k, rw[sel]), np.repeat(P[n][sel, k], rw[sel]))
            out[n] = o
        return out

    full = stats(np.arange(R))
    rng = np.random.default_rng(seed)
    draws = [stats(rng.integers(0, R, R)) for _ in range(boot)]
    rows = []
    for a, b in itertools.combinations(names, 2):
        for m in full[a]:
            va, vb = full[a][m], full[b][m]
            d = np.array([x[a][m] - x[b][m] for x in draws], float)
            d = d[np.isfinite(d)]
            lo, hi = (np.percentile(d, [2.5, 97.5]) if len(d) else (np.nan, np.nan))
            bi = best_of(m, [va, vb])
            rows.append(dict(a=a, b=b, metric=m, value_a=va, value_b=vb, a_minus_b=va - vb, lo=float(lo),
                             hi=float(hi), better="" if bi is None else (a, b)[bi],
                             clear=bool(np.isfinite(lo) and np.isfinite(hi) and (lo > 0 or hi < 0))))
    return pd.DataFrame(rows), len(y), R


# ===================================================================== report
def _fmt(v, nd=4):
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "—"
    if isinstance(v, (int, np.integer)):
        return f"{v:,}"
    return f"{v:.{nd}f}"


def side_rows(section, items, blocks, names):
    rows = []
    for key, label in items:
        vals = [_num((blocks.get(n) or {}).get(key)) for n in names]
        if all(v is None for v in vals):
            continue
        bi = best_of(key, vals)
        rows.append(dict(section=section, metric=key, label=label, **{f"value:{n}": v for n, v in zip(names, vals)},
                         best="" if bi is None else names[bi]))
    return rows


def label_rows(summaries, names):
    by = {n: {r["label"]: r for r in (summaries[n].get("other_labels") or [])} for n in names}
    order = []
    for n in names:
        order += [lab for lab in by[n] if lab not in order]
    rows = []
    for lab in order:
        for key, label in (("skill", "skill"), ("auroc", "AUROC"), ("calibration_error", "calibration error"),
                           ("brier", "Brier")):
            vals = [_num(by[n].get(lab, {}).get(key)) for n in names]
            if all(v is None for v in vals):
                continue
            bi = best_of(key, vals)
            rows.append(dict(section="Other labels", metric=f"{lab}:{key}", label=f"{lab}: {label}",
                             **{f"value:{n}": v for n, v in zip(names, vals)}, best="" if bi is None else names[bi]))
    return rows


def _img(path):
    with open(path, "rb") as fh:
        return "data:image/png;base64," + base64.b64encode(fh.read()).decode("ascii")


def table_html(rows, cols, heads):
    out = ['<div class="scroll"><table><thead><tr>' + "".join(f"<th>{h}</th>" for h in heads) + "</tr></thead><tbody>"]
    for r in rows:
        out.append("<tr>" + "".join(f"<td>{c(r) if callable(c) else html.escape(str(r.get(c, '')))}</td>"
                                    for c in cols) + "</tr>")
    out.append("</tbody></table></div>")
    return "".join(out)


def write_report(args, names, stats, agree, source, notes, summaries, side, pair, n_pairs, n_repos, per_repo):
    esc = {n: html.escape(n) for n in names}
    idx = {n: i for i, n in enumerate(names)}

    def tag(n):
        return f'<span class="w r{idx[n] % len(COLOURS)}">{esc[n]}</span>' if n else ""

    def val(n):
        return lambda r: _fmt(r.get(f"value:{n}"))

    sections = []
    for sec in dict.fromkeys(r["section"] for r in side):
        rs = [r for r in side if r["section"] == sec]
        sections.append(f"<h2>{html.escape(sec)}</h2>" + table_html(
            rs, ["label"] + [val(n) for n in names] + [lambda r: tag(r["best"])], [""] + [tag(n) for n in names]
            + ["best"]))
    pr = pair.to_dict("records")
    paired_html = table_html(pr, [lambda r: f"{tag(r['a'])} vs {tag(r['b'])}", "metric",
                                  lambda r: _fmt(r["value_a"]), lambda r: _fmt(r["value_b"]),
                                  lambda r: _fmt(r["a_minus_b"]), lambda r: f"{_fmt(r['lo'])} to {_fmt(r['hi'])}",
                                  lambda r: (tag(r["better"]) + ("" if r["clear"] else
                                             ' <span class="muted">(within noise)</span>')) if r["better"] else ""],
                             ["pair", "metric", "first", "second", "first − second", "95% range of the difference",
                              "better"])
    checks_by = {n: {r["check"]: r for r in (summaries[n].get("checklist") or [])} for n in names}
    order = []
    for n in names:
        order += [c for c in checks_by[n] if c not in order]

    def res(n):
        def f(r):
            v = checks_by[n].get(r["check"])
            if not v:
                return "—"
            cls = {"PASS": "pass", "FAIL": "fail"}.get(v.get("result"), "na")
            return (f'<span class="{cls}">{html.escape(v.get("result", ""))}</span>'
                    f'<div class="muted small">{html.escape(v.get("detail", ""))}</div>')
        return f
    check_html = table_html([dict(check=c) for c in order], ["check"] + [res(n) for n in names],
                            [""] + [tag(n) for n in names])
    repo_cols = ["repo", lambda r: _fmt(r.get("prs"))]
    repo_heads = ["repo", "PRs"]
    for n in names:
        repo_cols.append(lambda r, n=n: _fmt(r.get(f"brier:{n}")))
        repo_heads.append(f"Brier {tag(n)}")
    for n in names:
        repo_cols.append(lambda r, n=n: _fmt(r.get(f"skill:{n}")))
        repo_heads.append(f"skill {tag(n)}")
    repo_cols.append(lambda r: tag(r.get("best", "")))
    repo_heads.append("best (Brier)")
    repo_html = table_html(per_repo.to_dict("records"), repo_cols, repo_heads)
    wins = per_repo["best"].value_counts()
    wins_txt = ", ".join(f"{esc[n]} {int(wins.get(n, 0))}" for n in names)
    agree_html = (table_html(agree.sort_values("agreement").to_dict("records"),
                             ["column", lambda r: tag(r["run"]), lambda r: f"{r['agreement']:.2%}",
                              lambda r: _fmt(r["differing_prs"])],
                             ["column", "file", f"agreement with {esc[args.truth_from]}", "PRs differing"])
                  if len(agree) else "<p>No truth column is in more than one file.</p>")
    facts = [("PRs evaluated", "prs"), ("repos", "repos")]
    fact_rows = []
    for label, k in facts:
        fact_rows.append(dict(k=label, **{n: summaries[n].get(k) for n in names}))
    fact_rows.append(dict(k="seeds", **{n: (summaries[n].get("seeds") or {}).get("seeds") for n in names}))
    fact_rows.append(dict(k="seed-to-seed Brier spread",
                          **{n: (summaries[n].get("seeds") or {}).get("brier_spread") for n in names}))
    fact_rows.append(dict(k="latency p95, ms", **{n: (summaries[n].get("latency_ms") or {}).get("p95") for n in names}))
    fact_rows.append(dict(k="base rate used", **{n: summaries[n].get("base_rate_used") for n in names}))
    fact_html = table_html(fact_rows, ["k"] + [(lambda r, n=n: html.escape(r[n]) if isinstance(r[n], str)
                                                 else _fmt(r[n])) for n in names], [""] + [tag(n) for n in names])
    preds = {n: stats["pred"][n] for n in names}
    every = set.intersection(*[set(v) for v in preds.values()])
    pred_notes = "".join(f"<li>{tag(n)} only: {html.escape(', '.join(sorted(set(preds[n]) - every)) or 'nothing extra')}</li>"
                         for n in names)
    charts = []
    dirs = {n: os.path.join(args.out, f"eval_{slug(n)}", "charts") for n in names}
    chart_names = sorted(set().union(*[set(os.listdir(d)) if os.path.isdir(d) else set() for d in dirs.values()]))
    for c in chart_names:
        if not c.lower().endswith(".png"):
            continue
        cells = []
        for n in names:
            p = os.path.join(dirs[n], c)
            cells.append(f'<figure><img src="{_img(p)}" alt="{esc[n]}: {html.escape(c)}"><figcaption>{tag(n)}'
                         "</figcaption></figure>" if os.path.isfile(p) else
                         f'<figure class="none"><div>not produced</div><figcaption>{tag(n)}</figcaption></figure>')
        title = os.path.splitext(c)[0].split("_", 1)[-1].replace("_", " ")
        charts.append(f"<h3>{html.escape(title)}</h3><div class=\"grid\" title=\"Click to show one chart per row\" "
                      f"onclick=\"this.classList.toggle('stack')\">{''.join(cells)}</div>")
    notes_html = "".join(f"<li>{html.escape(n)}</li>" for n in notes)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    k = len(names)
    rc_light = "".join(f"--r{i}:{c};" for i, c in enumerate(COLOURS))
    rc_dark = "".join(f"--r{i}:{c};" for i, c in enumerate(COLOURS_DARK))
    rc_cls = "".join(f".w.r{i}{{color:var(--r{i})}}" for i in range(len(COLOURS)))
    css = f"""
:root{{--bg:#fbfbfa;--fg:#1d1d1b;--muted:#6b6b66;--line:#e2e1dc;--card:#ffffff;{rc_light}
--pass:#2f7d32;--fail:#b3261e;color-scheme:light;box-sizing:border-box;padding-top:env(safe-area-inset-top,0px);
padding-bottom:env(safe-area-inset-bottom,0px)}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--bg:#171716;--fg:#ecebe6;--muted:#a3a29b;
--line:#34332f;--card:#1f1f1d;{rc_dark}--pass:#7cc17f;--fail:#f08a83;color-scheme:dark}}}}
:root[data-theme="dark"]{{--bg:#171716;--fg:#ecebe6;--muted:#a3a29b;--line:#34332f;--card:#1f1f1d;{rc_dark}
--pass:#7cc17f;--fail:#f08a83;color-scheme:dark}}
html{{scroll-padding-top:env(safe-area-inset-top,0px)}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}}
main{{max-width:{1180 if k <= 2 else 1500}px;margin:0 auto;padding:24px 20px 64px}}
h1{{font-size:1.6rem;margin:0 0 4px}}h2{{font-size:1.15rem;margin:32px 0 8px;border-bottom:1px solid var(--line);
padding-bottom:4px}}h3{{font-size:1rem;margin:24px 0 8px;text-transform:capitalize}}
.muted{{color:var(--muted)}}.small{{font-size:.8rem}}.scroll{{overflow-x:auto}}
table{{border-collapse:collapse;width:100%;background:var(--card);font-variant-numeric:tabular-nums}}
th,td{{border-bottom:1px solid var(--line);padding:6px 10px;text-align:left;vertical-align:top}}
th{{font-weight:600;font-size:.85rem;color:var(--muted)}}
.w{{font-weight:600}}{rc_cls}.pass{{color:var(--pass);font-weight:600}}.fail{{color:var(--fail);font-weight:600}}
.na{{color:var(--muted)}}
.grid{{display:grid;grid-template-columns:repeat({k},minmax(0,1fr));gap:12px}}
.grid.stack{{grid-template-columns:1fr}}.grid{{cursor:zoom-in}}.grid.stack{{cursor:zoom-out}}
@media (max-width:{380 * k}px){{.grid{{grid-template-columns:1fr}}}}
figure{{margin:0;background:var(--card);border:1px solid var(--line);padding:8px}}figure img{{max-width:100%;
display:block;background:#fff}}figcaption{{font-size:.85rem;margin-top:4px}}
figure.none div{{padding:40px;text-align:center;color:var(--muted)}}
.legend span{{margin-right:16px}}ul{{padding-left:20px}}
"""
    title = " vs ".join(esc[n] for n in names)
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{title}: side-by-side evaluation</title><style>{css}</style></head><body><main>
<h1>{title}</h1>
<p class="muted">Same PRs, same truth columns, same eval_script.py. Generated {now} by
pipelines/evaluation/compare_runs.py. <span class="legend">{''.join(tag(n) for n in names)}</span></p>
<h2>How they were compared</h2><ul>{notes_html}</ul>
{fact_html}
<h2>Paired differences on the same PRs</h2>
<p class="muted">{n_pairs:,} PRs in {n_repos} repos, seeds averaged per PR. Brier and log loss are averaged per repo,
then over repos, as eval_script.py does. The 95% range comes from resampling whole repos {args.boot:,} times (the
same draws for every pair); a difference whose range includes 0 is within noise.</p>
{paired_html}
{''.join(sections)}
<h2>Checklist</h2>{check_html}
<h2>Per repo</h2><p class="muted">Best Brier per repo: {wins_txt}.</p>{repo_html}
<h2>Charts</h2><p class="muted">Each of eval_script.py's charts, one column per model. Click a row of charts to
show them one per row, full width; click again to go back.</p>
{''.join(charts)}
<h2>Truth columns</h2>
<p class="muted">Truth columns come from {esc[args.truth_from]} where its file has them, otherwise from the next file
that does. Agreement of the other files on the compared PRs:</p>{agree_html}
<p class="muted small">Prediction columns not in every file; a section with no predictions for a model shows — for it:</p>
<ul class="small">{pred_notes}</ul>
</main></body></html>"""
    with open(os.path.join(args.out, "comparison.html"), "w", encoding="utf-8") as fh:
        fh.write(doc)


def write_markdown(args, names, side, pair, notes, per_repo):
    lines = [f"# {' vs '.join(names)}", ""] + [f"- {n}" for n in notes] + ["", "## Paired differences (same PRs)", "",
             "| pair | metric | first | second | first − second | 95% range | better |",
             "|---|---|---|---|---|---|---|"]
    for r in pair.to_dict("records"):
        who = r["better"] + ("" if r["clear"] or not r["better"] else " (within noise)")
        lines.append(f"| {r['a']} vs {r['b']} | {r['metric']} | {_fmt(r['value_a'])} | {_fmt(r['value_b'])} | "
                     f"{_fmt(r['a_minus_b'])} | {_fmt(r['lo'])} to {_fmt(r['hi'])} | {who} |")
    lines += ["", "## eval_script.py, side by side", "", "| | " + " | ".join(names) + " | best |",
              "|---" * (len(names) + 2) + "|"]
    for r in side:
        lines.append(f"| {r['label']} | " + " | ".join(_fmt(r.get(f'value:{n}')) for n in names) + f" | {r['best']} |")
    wins = per_repo["best"].value_counts()
    lines += ["", "Best Brier per repo: " + ", ".join(f"{n} {int(wins.get(n, 0))}" for n in names) + "."]
    with open(os.path.join(args.out, "comparison.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


# ===================================================================== run
def run(args):
    names = list(args.runs)
    os.makedirs(os.path.join(args.out, "inputs"), exist_ok=True)
    if not os.path.isfile(EVAL_SCRIPT):
        raise SystemExit(f"cannot find {EVAL_SCRIPT}")
    print("Reading the prediction files ...", flush=True)
    frames = {n: pd.read_csv(p, low_memory=False) for n, p in args.runs.items()}
    pred = {n: [c for c in d.columns if c.startswith("p_")] for n, d in frames.items()}
    inputs, stats, agree, source, notes = align(frames, args.truth_from)
    stats["pred"] = pred
    notes.append(f"Truth columns (labels, reasons, dates, groups) come from {args.truth_from} where its file has them, "
                 "so every model is scored against the same answers.")
    bad = agree[agree["agreement"] < 0.999] if len(agree) else agree
    if len(bad):
        notes.append("Truth columns that differ between the files: " + "; ".join(
            f"{r.column} in {r.run}'s file ({r.agreement:.1%} agree)" for r in bad.sort_values("agreement").itertuples())
            + ". Each model was trained on its own file's version of these.")
    notes.append("Base rate: " + (f"the same --train file for all ({args.train})." if args.train else
                                  "taken from the compared PRs themselves, the same for all (no --train)."))
    paths, summaries = {}, {}
    for n in names:
        paths[n] = os.path.join(args.out, "inputs", f"{slug(n)}.csv")
        inputs[n].to_csv(paths[n], index=False)
    for n in names:
        summaries[n] = run_eval(paths[n], os.path.join(args.out, f"eval_{slug(n)}"), n, args)
    kept = {n: summaries[n].get("prs") for n in names}
    if len(set(kept.values())) > 1:
        notes.append("eval_script.py kept different numbers of PRs: " + ", ".join(f"{n} {v}" for n, v in kept.items()))
    print(f"  paired differences ({args.boot:,} repo resamples) ...", flush=True)
    pair, n_pairs, n_repos = paired(paths, args.boot, args.seed, args.all_authors)
    side = (side_rows("Outcome", HEADLINE, {n: summaries[n].get("metrics") for n in names}, names)
            + side_rows("Review queue", QUEUE, {n: (summaries[n].get("review_queue") or {}).get("model") for n in names},
                        names)
            + side_rows("Why PRs go nowhere", WHY, {n: summaries[n].get("why_nowhere") for n in names}, names)
            + label_rows(summaries, names))
    per_repo = None
    for n in names:
        r = pd.read_csv(os.path.join(args.out, f"eval_{slug(n)}", "per_repo.csv"))
        r = r[["repo", "prs", "brier", "skill"]].rename(columns={"brier": f"brier:{n}", "skill": f"skill:{n}"})
        per_repo = r if per_repo is None else per_repo.merge(r.drop(columns="prs"), on="repo", how="outer")
    per_repo["best"] = [("" if (i := best_of("brier", [_num(row[f"brier:{n}"]) for n in names])) is None else names[i])
                        for _, row in per_repo.iterrows()]
    per_repo = per_repo.sort_values("prs", ascending=False)
    per_repo.to_csv(os.path.join(args.out, "per_repo.csv"), index=False)
    pair.to_csv(os.path.join(args.out, "paired.csv"), index=False)
    pd.DataFrame(side).to_csv(os.path.join(args.out, "metrics_side_by_side.csv"), index=False)
    write_report(args, names, stats, agree, source, notes, summaries, side, pair, n_pairs, n_repos, per_repo)
    write_markdown(args, names, side, pair, notes, per_repo)

    line = "=" * 100
    print(f"\n{line}\n{' vs '.join(names)} on the same {n_pairs:,} PRs\n{line}")
    for n in notes:
        print("  " + n)
    print("\nPaired differences, first - second (95% range from resampling repos):")
    for r in pair.to_dict("records"):
        who = r["better"] or "-"
        print(f"  {r['a'] + ' vs ' + r['b']:<28} {r['metric']:<14} {_fmt(r['value_a'])}  {_fmt(r['value_b'])}  "
              f"{_fmt(r['a_minus_b']):>8}  [{_fmt(r['lo'])}, {_fmt(r['hi'])}]  better: {who}"
              f"{'' if r['clear'] or who == '-' else ' (within noise)'}")
    wins = per_repo["best"].value_counts()
    print("\nBest Brier per repo: " + ", ".join(f"{n} {int(wins.get(n, 0))}" for n in names))
    print(f"\n  {os.path.join(args.out, 'comparison.html')}  (the side-by-side report)")
    print(f"  {os.path.join(args.out, 'comparison.md')}, metrics_side_by_side.csv, paired.csv, per_repo.csv")


def _pairs(items, what):
    out = {}
    for it in items or []:
        if "=" not in it:
            raise SystemExit(f"{what} takes NAME=PATH, got {it!r}")
        n, p = it.split("=", 1)
        n = n.strip()
        if not n or n in out:
            raise SystemExit(f"{what}: names must be given and different ({it!r})")
        out[n] = p.strip()
    return out


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], epilog=__doc__.split("\n\n", 1)[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", action="append", metavar="NAME=PATH", help="a model's predictions (two or more)")
    ap.add_argument("--a", help="shorthand: model A's predictions (with --b)")
    ap.add_argument("--b", help="shorthand: model B's predictions")
    ap.add_argument("--a-name", default="A")
    ap.add_argument("--b-name", default="B")
    ap.add_argument("--out", required=True, help="folder for the report")
    ap.add_argument("--truth-from", help="the run whose truth columns all are scored on (default the first; "
                                         "'a' or 'b' with the shorthand)")
    ap.add_argument("--train", help="one training file for every evaluation's base rate (optional)")
    ap.add_argument("--features", action="append", metavar="NAME=PATH",
                    help="a run's features_used.txt, for the banned-columns check (optional)")
    ap.add_argument("--a-features", help="shorthand for --features A=PATH")
    ap.add_argument("--b-features", help="shorthand for --features B=PATH")
    ap.add_argument("--all-authors", action="store_true", help="passed to eval_script.py for every run")
    ap.add_argument("--boot", type=int, default=2000, help="resamples, for eval_script.py and the paired ranges")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)
    runs = _pairs(args.run, "--run")
    if args.a or args.b:
        if not (args.a and args.b):
            raise SystemExit("--a and --b go together")
        for n, p in ((args.a_name, args.a), (args.b_name, args.b)):
            if n in runs:
                raise SystemExit(f"two runs are called {n}")
            runs[n] = p
    if len(runs) < 2:
        raise SystemExit("give at least two runs (--run NAME=PATH, or --a and --b)")
    if len({slug(n) for n in runs}) < len(runs):
        raise SystemExit("run names must stay different once spaces and symbols are removed")
    args.runs = runs
    tf = args.truth_from
    if tf in ("a", "b") and tf not in runs and (args.a or args.b):
        tf = args.a_name if tf == "a" else args.b_name
    args.truth_from = tf or next(iter(runs))
    if args.truth_from not in runs:
        raise SystemExit(f"--truth-from {args.truth_from!r} is not one of the runs: {', '.join(runs)}")
    feats = _pairs(args.features, "--features")
    if args.a_features:
        feats[args.a_name] = args.a_features
    if args.b_features:
        feats[args.b_name] = args.b_features
    args.features = feats
    return args


def main(argv=None):
    args = parse_args(argv)
    args.out = os.path.normpath(args.out)
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    run(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
