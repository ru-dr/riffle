#!/usr/bin/env python3
"""
null_blame_timeouts.py — null the blame columns of PRs whose blame timed out under miner < 7.2.0.

    python null_blame_timeouts.py --dataset dataset_pc2of6.parquet [--traces 'logs/*.trace.jsonl']
    python null_blame_timeouts.py --dataset dataset_pc2of6.parquet --apply

Before 7.2.0 a blame that timed out was written as 0 / 0.0 in the line and ownership features,
not null. A re-mine of a big repo is slow, so this fixes only the PRs the run traces name: every
`git` `call` event with `exc=TimeoutExpired` and `cmd=blame` carries `repo` and `pr`. For each such
(repo, PR) the ten blame-derived aggregate columns are set to null (unobserved, which is what they
should have been). The rows stay otherwise as they were.

Dry run by default: prints the PRs and columns and writes nothing. --apply keeps the original as
<dataset>.pre-blame-null.bak and replaces the dataset atomically. Rows can only be nulled: values
are not recomputed (the miner has no per-PR re-mine); a re-mine of the repo restores them.

Limits: a trace written before the `repo` and `pr` fields (miner < 7.0) names no PR, so its timeouts
are reported as "unattributed" and need a re-mine of the repo. Only the dataset table is touched.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

# aggregate.AGG_SPEC applied to the blame-derived per-file features (line_history, and the ownership
# columns of history.per_file_features); `label_abandoned` is an outcome, not one of these
BLAME_COLUMNS = (
    "max_file_top_author_share", "max_file_minor_contributor_count", "max_abandoned_lines_share",
    "min_mod_line_age_days_min", "median_mod_line_age_days_median", "max_mod_lines_distinct_authors",
    "mean_mod_lines_self_authored_share", "max_mod_lines_prior_fix_share",
    "max_hunk_fix_proximity", "mean_hunk_fix_proximity",
)


def timed_out_prs(trace_globs: list[str]) -> tuple[dict[str, set[int]], int]:
    """({repo (lower case): {PR numbers}}, timeouts that name no PR) from the run traces."""
    prs: dict[str, set[int]] = {}
    unattributed = 0
    for pattern in trace_globs:
        for path in sorted(glob.glob(pattern)):
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if '"blame"' not in line or "TimeoutExpired" not in line:
                        continue
                    try:
                        ev = json.loads(line)
                    except ValueError:
                        continue
                    if ev.get("exc") != "TimeoutExpired" or ev.get("cmd") != "blame":
                        continue
                    if ev.get("repo") and ev.get("pr") is not None:
                        prs.setdefault(str(ev["repo"]).lower(), set()).add(int(ev["pr"]))
                    else:
                        unattributed += 1
    return prs, unattributed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dataset", required=True, help="dataset parquet to fix (one PC's or merged)")
    ap.add_argument("--traces", action="append", default=None,
                    help="glob of run traces (repeatable; default logs/*.trace.jsonl)")
    ap.add_argument("--apply", action="store_true", help="write the change (default: dry run)")
    args = ap.parse_args()

    import pandas as pd

    prs, unattributed = timed_out_prs(args.traces or ["logs/*.trace.jsonl"])
    df = pd.read_parquet(args.dataset)
    cols = [c for c in BLAME_COLUMNS if c in df.columns]
    missing = sorted(set(BLAME_COLUMNS) - set(cols))
    if missing:
        print(f"note: columns not in this dataset, skipped: {', '.join(missing)}")

    repo_l = df["repo"].astype(str).str.lower()
    hit = pd.Series(False, index=df.index)
    for repo, nums in prs.items():
        hit |= (repo_l == repo) & df["pr_number"].isin(nums)

    print(f"trace: {sum(len(v) for v in prs.values())} PRs with a blame timeout "
          f"in {len(prs)} repos; {unattributed} timeouts name no PR (re-mine those repos)")
    for repo, nums in sorted(prs.items()):
        n_here = int(((repo_l == repo) & df["pr_number"].isin(nums)).sum())
        print(f"  {repo}: {len(nums)} PRs in trace, {n_here} rows in this dataset")
    changing = int((df.loc[hit, cols].fillna(-1) != -1).any(axis=1).sum()) if hit.any() else 0
    print(f"rows to null: {int(hit.sum())} ({changing} hold a value in a blame column); "
          f"columns: {', '.join(cols)}")
    if not hit.any():
        return 0
    if not args.apply:
        print("dry run: nothing written. Add --apply to write.")
        return 0

    for c in cols:
        if pd.api.types.is_integer_dtype(df[c]):
            df[c] = df[c].astype("Int64")       # an integer column stays integer, with nulls
    df.loc[hit, cols] = None
    bak = args.dataset + ".pre-blame-null.bak"
    if os.path.exists(bak):
        print(f"refusing: {bak} exists (an earlier run); move it first", file=sys.stderr)
        return 1
    tmp = args.dataset + ".tmp"
    df.to_parquet(tmp, index=False)
    os.replace(args.dataset, bak)
    os.replace(tmp, args.dataset)
    print(f"done: {int(hit.sum())} rows nulled; original kept as {bak}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
