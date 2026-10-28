#!/usr/bin/env python3
"""
publish.py — check the merged tables against their contracts, then version them with DVC.

    python publish.py check  --dir . [--version 7.0.1]
    python publish.py push   --dir . --workspace dvc_ws --remote gs://bucket/riffle [--version 7.0.1]

`check` reads <stem>_merged.parquet, <stem>_merged_snapshots.parquet and
<stem>_merged_commit_features.parquet (what merge_shards.py writes) and fails when a
table's columns differ from its schema in contracts/, either way, or when the
merge manifest's miner version is not --version.

`push` copies those files and the manifest into <workspace>/mined/<miner_version>/,
runs `dvc add mined` and `dvc push`, and leaves <workspace>/mined.dvc: the pointer a person
commits under data/. Only merged outputs are tracked; per-PC shard files travel by
sync.py (ADR 0015). The workspace is a plain directory made with `dvc init --no-scm`, so
DVC never needs a git checkout or credentials for it; the remote uses the VM's
service account, not a key file.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CONTRACTS = os.environ.get("RIFFLE_CONTRACTS_DIR") or os.path.join(HERE, "..", "..", "..", "contracts")
TABLES = {"dataset": "mined_dataset", "snapshots": "mined_snapshot", "commit_features": "mined_commit_features"}


def merged_files(directory: str) -> dict[str, str]:
    """kind -> path of the merged parquet, or a ValueError naming what is missing."""
    found = {}
    for kind in TABLES:
        suffix = "_merged.parquet" if kind == "dataset" else f"_merged_{kind}.parquet"
        hits = sorted(glob.glob(os.path.join(directory, f"*{suffix}")))
        if len(hits) != 1:
            raise ValueError(f"expected one *{suffix} in {directory}, found {len(hits)}")
        found[kind] = hits[0]
    return found


def schema_columns(contract: str, contracts_dir: str = CONTRACTS) -> set[str]:
    with open(os.path.join(contracts_dir, f"{contract}.schema.json"), encoding="utf-8") as fh:
        return set(json.load(fh)["properties"])


def parquet_columns(path: str) -> set[str]:
    import pyarrow.parquet as pq
    return set(pq.read_schema(path).names)


def check(directory: str, version: str | None = None, contracts_dir: str = CONTRACTS) -> list[str]:
    """Every problem found, as a sentence; empty means the tables match their contracts."""
    problems = []
    files = merged_files(directory)
    for kind, contract in TABLES.items():
        want, have = schema_columns(contract, contracts_dir), parquet_columns(files[kind])
        if want - have:
            problems.append(f"{kind}: missing columns {sorted(want - have)[:8]}")
        if have - want:
            problems.append(f"{kind}: columns not in {contract}: {sorted(have - want)[:8]}")
    if version:
        manifests = glob.glob(os.path.join(directory, "*_merged.merge.json"))
        if not manifests:
            problems.append("no *_merged.merge.json: the miner version of these tables is unknown")     # unobserved is not a pass
        for man in manifests:
            with open(man, encoding="utf-8") as fh:
                seen = json.load(fh).get("miner_version")
            if seen != [version]:
                problems.append(f"{os.path.basename(man)}: miner_version {seen!r}, wanted {version!r}")
    return problems


def push(directory: str, workspace: str, remote: str, version: str, run=subprocess.run) -> str:
    """Copy the merged files into the DVC workspace, add and push them. Returns the pointer's text."""
    files = list(merged_files(directory).values()) + glob.glob(os.path.join(directory, "*_merged.merge.json"))
    dest = os.path.join(workspace, "mined", version)
    shutil.rmtree(dest, ignore_errors=True)                 # only this run's files are versioned, never leftovers of an earlier push
    os.makedirs(dest, exist_ok=True)
    for src in files:
        shutil.copy2(src, dest)
    if not os.path.isdir(os.path.join(workspace, ".dvc")):
        run(["dvc", "init", "--no-scm"], cwd=workspace, check=True)
    run(["dvc", "remote", "add", "-f", "-d", "store", remote], cwd=workspace, check=True)
    run(["dvc", "add", "mined"], cwd=workspace, check=True)
    run(["dvc", "push"], cwd=workspace, check=True)
    with open(os.path.join(workspace, "mined.dvc"), encoding="utf-8") as fh:
        return fh.read()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("check", "push"):
        p = sub.add_parser(name)
        p.add_argument("--dir", default=".")
        p.add_argument("--version", default=None)
    sub.choices["push"].add_argument("--workspace", required=True)
    sub.choices["push"].add_argument("--remote", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "check":
        problems = check(a.dir, a.version)
        for line in problems:
            print(f"[publish] {line}", file=sys.stderr)
        print("[publish] tables match their contracts" if not problems else f"[publish] {len(problems)} problem(s)")
        return 1 if problems else 0
    if not a.version:
        ap.error("push needs --version")
    print(push(a.dir, a.workspace, a.remote, a.version))
    return 0


if __name__ == "__main__":
    sys.exit(main())
