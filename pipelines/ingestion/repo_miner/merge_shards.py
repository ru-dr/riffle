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
  - no repo appears on two PCs, and no PR, snapshot or commit row repeats,
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
    return pd.read_parquet(path) if path.endswith(".parquet") else pd.read_json(path, lines=True)


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


def check_manifests(shards: list[dict], allow_version_mix: bool = False) -> list[str]:
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
            if r in seen:
                problems.append(f"repo {r} is on PC {seen[r]} and PC {s['pc']}")
            seen[r] = s["pc"]
    if len({bool(s["files"].get("snapshots")) for s in shards}) > 1:
        problems.append("some PCs have a snapshots table and some don't")
    return problems


def merge(out: str, directory: str = ".", allow_version_mix: bool = False) -> dict:
    shards = find_shards(out, directory)
    problems = check_manifests(shards, allow_version_mix)
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
        df = pd.concat([_read(os.path.join(s["_dir"], nm)) for s, nm in
                        sorted(zip(shards, names), key=lambda p: p[0]["pc"])], ignore_index=True)
        dup = df.duplicated(KEYS[kind]).sum()
        if dup:
            raise SystemExit(f"cannot merge: {dup} repeated {kind} rows on {KEYS[kind]}")
        frames[kind] = df
    if "snapshots" in frames:
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
    a = ap.parse_args()
    r = merge(a.out, a.dir, a.allow_version_mix)
    for kind, n in r["rows"].items():
        print(f"[merge] {kind}: {n:,} rows -> {r['outputs'][kind]}", file=sys.stderr)


if __name__ == "__main__":
    main()
