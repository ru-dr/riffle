#!/usr/bin/env python3
"""
merge_shards.py — join the datasets the PCs of a --shard run wrote into one.

    python merge_shards.py --out dataset.parquet [--dir .]

Each PC's run leaves dataset_pc1of3.parquet, dataset_pc1of3_snapshots.parquet,
dataset_pc1of3_commit_features.parquet and dataset_pc1of3.shard.json (naming.py,
build_dataset.py). This reads the manifests, refuses to merge unless:
  - every PC 1..N is present (none missing, one split),
  - all PCs used the same split ID (same repos file and PC count),
  - all PCs ran the same miner version (--allow-version-mix to override),
  - no manifest lists a failed repo, and each PC's dataset holds exactly the repos its
    manifest lists (--allow-incomplete to merge a shard that is missing repos anyway),
  - no repo (compared in lower case) appears on two PCs, and no PR, snapshot or commit row repeats,
  - the merged snapshots still match the merged dataset (consistency.py),
then writes <stem>_merged<ext>, <stem>_merged_snapshots<ext>,
<stem>_merged_commit_features<ext> and <stem>_merged.merge.json (sources, counts).
The tables stay three: each has its own grain (one row per PR, per snapshot, per
head commit); see the README before stacking them.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import time

import consistency
import naming

KINDS = ("dataset", "snapshots", "commit_features")
KEYS = {"dataset": ["repo", "pr_number"], "snapshots": ["snapshot_id"],
        "commit_features": ["repo", "pr_number", "head_sha"]}


def _read(path: str):
    import pandas as pd
    if path.endswith(".parquet"):
        return pd.read_parquet(path)
    if os.path.getsize(path) == 0:                 # an empty table written without rows
        return pd.DataFrame()
    return pd.read_json(path, lines=True)


def _write(df, path: str) -> None:
    if path.endswith(".parquet"):
        df.to_parquet(path, index=False)
    else:
        df.to_json(path, orient="records", lines=True, date_format="iso")


def find_shards(out: str, directory: str) -> list[dict]:
    """The manifests for `out`'s PCs, each with its own path and directory added."""
    stem = os.path.splitext(os.path.basename(out))[0]
    found = []
    for path in sorted(glob.glob(os.path.join(directory, f"{stem}_pc*of*.shard.json"))):
        if not re.fullmatch(re.escape(stem) + r"_pc\d+of\d+\.shard\.json", os.path.basename(path)):
            continue
        with open(path, encoding="utf-8") as fh:
            m = json.load(fh)
        found.append({**m, "_path": path, "_dir": os.path.dirname(path)})
    return found


def check_manifests(shards: list[dict], allow_version_mix: bool = False,
                    allow_incomplete: bool = False) -> list[str]:
    """Why these manifests can't be merged; an empty list means they can."""
    if not shards:
        return ["no shard manifests found (looking for <stem>_pcKofN.shard.json)"]
    problems = []
    sizes = {s["of"] for s in shards}
    if len(sizes) > 1:
        return [f"manifests from different PC counts: {sorted(sizes)}; merge one split at a time"]
    n = sizes.pop()
    have = sorted(s["pc"] for s in shards)
    if have != list(range(1, n + 1)):
        problems.append(f"PCs present {have}, need 1..{n}; missing {sorted(set(range(1, n + 1)) - set(have))}")
    if len({s.get("split_id") for s in shards}) > 1 or None in {s.get("split_id") for s in shards}:
        problems.append(f"split IDs differ or are missing: {sorted(str(s.get('split_id')) for s in shards)}; "
                        f"every PC needs the same repos file and PC count")
    versions = {s.get("miner_version") for s in shards}
    if len(versions) > 1 and not allow_version_mix:
        problems.append(f"miner versions differ: {sorted(map(str, versions))} (--allow-version-mix to merge anyway)")
    seen: dict = {}
    for s in shards:
        for r in s.get("repos", []):
            r = str(r).lower()                     # GitHub names are case-insensitive
            if r in seen and seen[r] != s["pc"]:
                problems.append(f"repo {r} is on PC {seen[r]} and PC {s['pc']}")
            seen[r] = s["pc"]
    if not allow_incomplete:
        for s in shards:
            if s.get("failed"):
                problems.append(f"PC {s['pc']} failed on {len(s['failed'])} repo(s): "
                                f"{', '.join(map(str, s['failed'][:5]))}"
                                f"{' ...' if len(s['failed']) > 5 else ''} "
                                f"(re-run them, or --allow-incomplete to merge anyway)")
    if len({bool(s["files"].get("snapshots")) for s in shards}) > 1:
        problems.append("some PCs have a snapshots table and some don't")
    return problems


def _repos(df) -> set[str]:
    return {str(r).lower() for r in df["repo"].dropna()} if "repo" in df.columns else set()


def merge(out: str, directory: str = ".", allow_version_mix: bool = False,
          allow_incomplete: bool = False) -> dict:
    shards = find_shards(out, directory)
    problems = check_manifests(shards, allow_version_mix, allow_incomplete)
    if problems:
        raise SystemExit("cannot merge:\n  - " + "\n  - ".join(problems))
    import pandas as pd
    stem, ext = os.path.splitext(out)
    merged_out = f"{stem}_merged{ext}"
    outs = {"dataset": merged_out, "snapshots": naming.snapshot_outs(merged_out)[0],
            "commit_features": naming.snapshot_outs(merged_out)[1]}
    frames: dict = {}
    for kind in KINDS:
        names = [s["files"].get(kind) for s in shards]
        if not any(names):
            continue
        missing = [os.path.join(s["_dir"], nm) for s, nm in zip(shards, names)
                   if not nm or not os.path.isfile(os.path.join(s["_dir"], nm))]
        if missing:
            raise SystemExit(f"cannot merge: missing {kind} file(s): {', '.join(missing)}")
        parts = [(s, _read(os.path.join(s["_dir"], nm))) for s, nm in
                 sorted(zip(shards, names), key=lambda p: p[0]["pc"])]
        if kind == "dataset":
            for s, part in parts:
                have, listed = _repos(part), {str(r).lower() for r in s.get("repos", [])}
                absent, extra = sorted(listed - have), sorted(have - listed)
                if extra:
                    raise SystemExit(f"cannot merge: PC {s['pc']}'s dataset has rows of repos its manifest "
                                     f"does not list: {', '.join(extra[:5])}")
                if absent and not allow_incomplete:
                    raise SystemExit(f"cannot merge: PC {s['pc']}'s dataset has no rows for {len(absent)} "
                                     f"repo(s) its manifest lists: {', '.join(absent[:5])} "
                                     f"(--allow-incomplete if they truly had no PRs)")
        df = pd.concat([part for _s, part in parts], ignore_index=True)
        if df.empty and not all(c in df.columns for c in KEYS[kind]):
            frames[kind] = df                      # every part was an empty table without columns
            continue
        dup = df.duplicated(KEYS[kind]).sum()
        if dup:
            raise SystemExit(f"cannot merge: {dup} repeated {kind} rows on {KEYS[kind]}")
        frames[kind] = df
    if "snapshots" in frames and not frames["snapshots"].empty and not frames["dataset"].empty:
        stale = consistency.stale_prs(frames["dataset"], frames["snapshots"])
        if stale["missing"] or stale["changed"]:
            raise SystemExit(f"cannot merge: the snapshots no longer match the dataset "
                             f"({len(stale['changed'])} PRs changed, {len(stale['missing'])} missing); "
                             f"rebuild them with snapshots.py on each PC")
    for kind, df in frames.items():
        _write(df, outs[kind])
    report = {"merged_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "of": shards[0]["of"],
              "split_id": shards[0]["split_id"], "miner_version": sorted({s["miner_version"] for s in shards}),
              "sources": [s["_path"] for s in shards], "outputs": {k: outs[k] for k in frames},
              "rows": {k: len(v) for k, v in frames.items()}}
    with open(f"{stem}_merged.merge.json", "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="the dataset name the PCs used with --out, e.g. dataset.parquet")
    ap.add_argument("--dir", default=".", help="where the PCs' files were gathered (default: here)")
    ap.add_argument("--allow-version-mix", action="store_true")
    ap.add_argument("--allow-incomplete", action="store_true",
                    help="merge shards whose manifest lists failed repos or whose dataset lacks listed repos")
    a = ap.parse_args()
    r = merge(a.out, a.dir, a.allow_version_mix, a.allow_incomplete)
    for kind, n in r["rows"].items():
        print(f"[merge] {kind}: {n:,} rows -> {r['outputs'][kind]}", file=sys.stderr)


if __name__ == "__main__":
    main()
