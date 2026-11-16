"""
model_gates.py - the checks a trained outcome model must pass before it is merged or registered (guidelines 7.2,
7.3 and 7.6). Reads only what the notebook and bias_report.py write to eval/, so CI can run it without the data.

    python pipelines/training/outcome/model_gates.py
    python pipelines/training/outcome/model_gates.py --baseline /tmp/main/pipelines/training/outcome/eval

Validation (both splits, from eval/<split>/summary.json)
  - eval_script.py's own checks that must pass: valid prediction file, no banned columns, beats the base rate overall
    and in every main group, honest probabilities, fast enough, 3 or more seeds. The two 'likely to go nowhere' list
    checks are reported but not required: on the small by-PR hold-out they fail as statistical ties.
  - skill at least SKILL_FLOOR, calibration error at most CALIBRATION_LIMIT, 95% of PRs scored within LATENCY_MS.
Bias (by repo, from eval/by_repo/bias/slices.csv)
  - newcomers' false-flag rate (landed, but predicted to go nowhere) at most NEWCOMER_FF_LIMIT, and at most
    NEWCOMER_RATIO_LIMIT times the returning authors' rate; newcomers' accuracy at most NEWCOMER_ACC_GAP below theirs.
Regression against a baseline (--baseline: the eval folder of the model now on main)
  - skill may not drop by more than MAX_SKILL_DROP, calibration error may not rise by more than MAX_CALIB_RISE, and
    the newcomer false-flag rate may not rise by more than MAX_FF_RISE. Without --baseline these are skipped.

Prints a table, appends it to $GITHUB_STEP_SUMMARY when set, writes --json if given, and exits 1 if any check fails.
Needs pandas.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SPLITS = ("by_repo", "by_pr")
REQUIRED_CHECKS = ("Prediction file is complete and valid", "Model reads no banned columns",
                   "Beats the base rate overall", "Beats the base rate in every main group",
                   "Probabilities are honest", "Fast enough", "Trained with 3 or more seeds")
SKILL_FLOOR = {"by_repo": 0.15, "by_pr": 0.20}
CALIBRATION_LIMIT = 0.03
LATENCY_MS = 200.0
NEWCOMER_FF_LIMIT = 0.40
NEWCOMER_RATIO_LIMIT = 4.0
NEWCOMER_ACC_GAP = 0.10
MAX_SKILL_DROP = 0.005
MAX_CALIB_RISE = 0.005
MAX_FF_RISE = 0.02


def load(eval_dir):
    """summary.json per split and the by-repo bias table; missing files come back as None."""
    out = {}
    for split in SPLITS:
        p = os.path.join(eval_dir, split, "summary.json")
        out[split] = json.load(open(p, encoding="utf-8")) if os.path.isfile(p) else None
    p = os.path.join(eval_dir, "by_repo", "bias", "slices.csv")
    out["bias"] = pd.read_csv(p) if os.path.isfile(p) else None
    return out


def author_rows(bias):
    """(newcomers, returning authors) rows of the bias table, or (None, None)."""
    if bias is None:
        return None, None
    a = bias[bias["slice"] == "author"].set_index("group")
    get = lambda g: a.loc[g] if g in a.index else None  # noqa: E731
    return get("first PR in repo"), get("returning")


def run_gates(eval_dir, baseline_dir=None):
    """Every check as a row: (area, check, value, limit, result) with result PASS, FAIL or SKIP."""
    rows = []

    def add(area, check, value, limit, ok, info=False):
        rows.append(dict(area=area, check=check, value=value, limit=limit,
                         result="INFO" if info else "SKIP" if ok is None else ("PASS" if ok else "FAIL")))

    cur = load(eval_dir)
    for split in SPLITS:
        s = cur[split]
        if s is None:
            add(f"validation {split}", "summary.json exists", "missing", "present", False)
            continue
        checks = {c["check"]: c for c in s.get("checklist", [])}
        for want in REQUIRED_CHECKS:
            hit = next((c for name, c in checks.items() if name.startswith(want)), None)
            res = hit["result"] if hit else "missing"
            add(f"validation {split}", f"eval_script: {want}", res, "PASS", res == "PASS")
        for name, c in checks.items():
            if name.startswith("'Likely to go nowhere'"):
                add(f"validation {split}", f"eval_script: {name} (reported only)", c["result"], "-", None, info=True)
        m = s.get("metrics", {})
        add(f"validation {split}", "skill vs base rate", round(m.get("skill", float("nan")), 4),
            f">= {SKILL_FLOOR[split]}", m.get("skill", -1) >= SKILL_FLOOR[split])
        add(f"validation {split}", "calibration error", round(m.get("calibration_error", float("nan")), 4),
            f"<= {CALIBRATION_LIMIT}", m.get("calibration_error", 1) <= CALIBRATION_LIMIT)
        p95 = (s.get("latency_ms") or {}).get("p95")
        add(f"validation {split}", "latency p95 (ms)", p95, f"<= {LATENCY_MS:g}",
            None if p95 is None else p95 <= LATENCY_MS)

    new, ret = author_rows(cur["bias"])
    if new is None or ret is None:
        add("bias", "by_repo/bias/slices.csv with author slices", "missing", "present", False)
    else:
        add("bias", "newcomer false-flag rate", round(new["false_flag_rate"], 4), f"<= {NEWCOMER_FF_LIMIT}",
            new["false_flag_rate"] <= NEWCOMER_FF_LIMIT)
        ratio = new["false_flag_rate"] / max(ret["false_flag_rate"], 1e-9)
        add("bias", "newcomer / returning false-flag ratio", round(ratio, 2), f"<= {NEWCOMER_RATIO_LIMIT}",
            ratio <= NEWCOMER_RATIO_LIMIT)
        gap = ret["accuracy"] - new["accuracy"]
        add("bias", "accuracy gap, returning minus newcomer", round(gap, 4), f"<= {NEWCOMER_ACC_GAP}",
            gap <= NEWCOMER_ACC_GAP)

    if baseline_dir and os.path.isdir(baseline_dir):
        base = load(baseline_dir)
        for split in SPLITS:
            b, s = base[split], cur[split]
            if b is None or s is None:
                add(f"regression {split}", "baseline summary.json", "missing", "present", None)
                continue
            d_skill = s["metrics"]["skill"] - b["metrics"]["skill"]
            add(f"regression {split}", "skill change vs main", round(d_skill, 4), f">= -{MAX_SKILL_DROP}",
                d_skill >= -MAX_SKILL_DROP)
            d_cal = s["metrics"]["calibration_error"] - b["metrics"]["calibration_error"]
            add(f"regression {split}", "calibration error change vs main", round(d_cal, 4),
                f"<= +{MAX_CALIB_RISE}", d_cal <= MAX_CALIB_RISE)
        bn, _ = author_rows(base["bias"])
        if bn is not None and new is not None:
            d_ff = new["false_flag_rate"] - bn["false_flag_rate"]
            add("regression bias", "newcomer false-flag change vs main", round(d_ff, 4), f"<= +{MAX_FF_RISE}",
                d_ff <= MAX_FF_RISE)
    else:
        add("regression", "baseline (model on main)", "not given", "-", None)
    return rows


def as_markdown(rows, ok):
    lines = [f"### Model gates: {'passed' if ok else 'FAILED'}", "", "| area | check | value | limit | result |",
             "|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['area']} | {r['check']} | {r['value']} | {r['limit']} | {r['result']} |")
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], epilog=__doc__.split("\n\n", 1)[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--eval", default=os.path.join(HERE, "eval"), help="the eval folder to check")
    ap.add_argument("--baseline", help="the eval folder of the model now on main, for the regression checks")
    ap.add_argument("--json", help="also write the rows here")
    args = ap.parse_args(argv)
    rows = run_gates(args.eval, args.baseline)
    ok = not any(r["result"] == "FAIL" for r in rows)
    md = as_markdown(rows, ok)
    print(md)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(md)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(dict(passed=ok, rows=rows), fh, indent=2, default=str)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
