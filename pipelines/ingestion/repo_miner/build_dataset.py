#!/usr/bin/env python3
"""
build_dataset.py — clone a list of GitHub repos and mine them all into ONE
growing dataset file.

Reads GitHub URLs (one per line) from a repos file, clones each into the clone
directory (removing any existing copy first — a fresh FULL clone, since the
extractor needs complete history; do not use --depth), then runs mine_repo.py on
each with --append so every repo's rows accumulate in a single output file,
de-duplicated by (repo, pr_number).

Usage:
    python build_dataset.py
        # reads repos.txt -> writes dataset.parquet, cloning into cloned_repos\\

    python build_dataset.py --repos-file repos.txt --out dataset.parquet ^
        --clone-dir cloned_repos --max-prs 200 [--ci-history] [--llm] [--skip-existing]

Flags:
    --repos-file   file with one GitHub URL per line (# comments allowed)
    --out          single dataset file rows are appended to (.parquet/.csv/.jsonl)
    --clone-dir    where repos are cloned (created if missing)
    --max-prs N    cap PRs per repo (passed through to the miner)
    --ci-history   build the historical CI-fail-rate features (slow; passed through)
    --llm          enable the LLM semantic flags (needs LLM_API_KEY; passed through)
    --skip-existing  reuse an already-cloned repo instead of re-cloning (faster re-runs)
    --keep-clones  do NOT delete each clone after mining it (default: delete, so
                   only one repo's clone is on disk at a time)
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import stat
import subprocess
import sys
import time


def _parse_url(line: str):
    """(owner, name, url) from a GitHub URL line, or None for blanks/comments/bad."""
    url = line.strip()
    if not url or url.startswith("#"):
        return None
    m = re.search(r"github\.com[:/]+([^/]+)/(.+?)(?:\.git)?/?$", url)
    if not m:
        return None
    return m.group(1), m.group(2), url


def _rmtree(path: str) -> None:
    """Remove a tree, handling Windows' read-only .git files."""
    def onerror(func, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except Exception:
            pass
    shutil.rmtree(path, onerror=onerror)


def main():
    ap = argparse.ArgumentParser(
        description="Clone a list of GitHub repos and mine them into one dataset.")
    ap.add_argument("--repos-file", default="repos.txt",
                    help="file with one GitHub URL per line (# comments allowed)")
    ap.add_argument("--out", default="dataset.parquet",
                    help="single output dataset (rows appended, deduped)")
    ap.add_argument("--clone-dir", default="cloned_repos",
                    help="directory to clone repos into")
    ap.add_argument("--max-prs", type=int, default=None)
    ap.add_argument("--ci-history", action="store_true")
    ap.add_argument("--szz", action="store_true")
    ap.add_argument("--declared-labels", action="store_true")
    ap.add_argument("--pr-discovery", choices=["api", "git"], default="api")
    ap.add_argument("--llm", action="store_true")
    ap.add_argument("--skip-existing", action="store_true",
                    help="reuse an existing clone instead of re-cloning")
    ap.add_argument("--keep-clones", action="store_true",
                    help="keep each clone after mining (default: delete it before "
                         "cloning the next, so only one clone is on disk at a time)")
    ap.add_argument("--repo-retries", type=int, default=2,
                    help="extra attempts per repo if clone or mine fails (default 2, "
                         "so 3 total). The miner checkpoints, so a retried mine "
                         "resumes rather than redoing finished PRs.")
    args = ap.parse_args()

    if not os.path.isfile(args.repos_file):
        sys.exit(f"repos file not found: {args.repos_file}")

    with open(args.repos_file, encoding="utf-8") as fh:
        entries = [p for p in (_parse_url(line) for line in fh) if p]
    if not entries:
        sys.exit("no valid GitHub URLs found in the repos file")

    os.makedirs(args.clone_dir, exist_ok=True)
    here = os.path.dirname(os.path.abspath(__file__))
    miner = os.path.join(here, "mine_repo.py")

    print(f"[batch] {len(entries)} repos -> {args.out}\n", flush=True)
    attempts = max(1, args.repo_retries + 1)
    ok, failed = 0, []
    for i, (owner, name, url) in enumerate(entries, 1):
        target = os.path.join(args.clone_dir, f"{owner}_{name}")
        print(f"=== [{i}/{len(entries)}] {owner}/{name} ===", flush=True)

        # --- clone (retried) — one clone kept across mine retries -------------
        if os.path.isdir(target):
            if args.skip_existing:
                print(f"[clone] reusing existing {target}", flush=True)
            else:
                print(f"[clone] removing existing {target}", flush=True)
                _rmtree(target)
        cloned = os.path.isdir(target)
        for c_attempt in range(1, attempts + 1):
            if cloned:
                break
            print(f"[clone] git clone {url} (attempt {c_attempt}/{attempts})", flush=True)
            rc = subprocess.run(["git", "clone", url, target]).returncode
            if rc == 0:
                cloned = True
                break
            print(f"[clone] FAILED (exit {rc})", flush=True)
            if os.path.isdir(target):
                _rmtree(target)                      # clear a partial clone
            if c_attempt < attempts:
                time.sleep(min(120, 15 * c_attempt))
        if not cloned:
            print("[clone] giving up after retries; skipping repo\n", flush=True)
            failed.append(f"{owner}/{name} (clone)")
            continue

        # --- mine (retried; checkpoint makes a retry resume, not redo) --------
        cmd = [sys.executable, miner, "--repo", target,
               "--out", args.out, "--append"]
        if args.max_prs is not None:
            cmd += ["--max-prs", str(args.max_prs)]
        if args.ci_history:
            cmd.append("--ci-history")
        if args.szz:
            cmd.append("--szz")
        if args.declared_labels:
            cmd.append("--declared-labels")
        if args.pr_discovery != "api":
            cmd += ["--pr-discovery", args.pr_discovery]
        if args.llm:
            cmd.append("--llm")

        mined = False
        for m_attempt in range(1, attempts + 1):
            rc = subprocess.run(cmd).returncode
            if rc == 0:
                ok += 1
                mined = True
                break
            print(f"[mine] FAILED (exit {rc}) attempt {m_attempt}/{attempts}",
                  flush=True)
            if m_attempt < attempts:
                time.sleep(min(120, 15 * m_attempt))
        if not mined:
            print("[mine] giving up after retries; moving on", flush=True)
            failed.append(f"{owner}/{name} (mine)")

        # delete the clone before the next repo so disk never piles up (one clone
        # on disk at a time). Mined rows are already in --out; per-repo caches
        # (ci_cache/) and the .partial.jsonl checkpoint are kept. --keep-clones
        # opts out; so does --skip-existing (which exists to reuse clones).
        if not args.keep_clones and not args.skip_existing and os.path.isdir(target):
            print(f"[clone] removing {target} (mining done)", flush=True)
            _rmtree(target)
        print(flush=True)

    print(f"[batch] done: {ok}/{len(entries)} repos mined -> {args.out}")
    if failed:
        print("[batch] failures:")
        for f in failed:
            print("  -", f)


if __name__ == "__main__":
    main()
