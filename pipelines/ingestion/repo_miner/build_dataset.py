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
    --llm          enable the LLM semantic flags (headless Claude Code by default; passed through)
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

import progress


TEST_OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "test_output", "test_dataset.jsonl")


def _write_test_parquet(jsonl_path: str) -> None:
    """Test runs also save the same rows as parquet (what a real run writes),
    so the parquet path can be checked before mining the full corpus."""
    out = jsonl_path.rsplit(".", 1)[0] + ".parquet"
    try:
        import pandas as pd
        df = pd.read_json(jsonl_path, lines=True, dtype=False)
        df.to_parquet(out, index=False)
        back = pd.read_parquet(out)
        print(f"[test] parquet: {out} ({back.shape[0]} rows x {back.shape[1]} cols)", flush=True)
    except ImportError:
        print("[test] parquet skipped: pip install pandas pyarrow", flush=True)
    except Exception as e:                                  # noqa: BLE001
        print(f"[test] parquet FAILED: {type(e).__name__}: {e}", flush=True)


def _parse_url(line: str):
    """(owner, name, url) from a GitHub URL line, or None for blanks/comments/bad."""
    url = line.split("#", 1)[0].strip()       # drop inline "# Train" style comments
    if not url:
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
    ap.add_argument("--no-scanners", action="store_true")
    ap.add_argument("--no-api-contract", action="store_true")
    ap.add_argument("--no-review-history", action="store_true")
    ap.add_argument("--llm-model", default=None, help="e.g. sonnet, opus, haiku (passed through)")
    ap.add_argument("--workers", type=int, default=None,
                    help="PRs mined in parallel per repo (passed through)")
    ap.add_argument("--llm-concurrency", type=int, default=None,
                    help="max concurrent LLM calls (passed through)")
    ap.add_argument("--skip-existing", action="store_true",
                    help="reuse an existing clone instead of re-cloning")
    ap.add_argument("--keep-clones", action="store_true",
                    help="keep each clone after mining (default: delete it before "
                         "cloning the next, so only one clone is on disk at a time)")
    ap.add_argument("--repo-retries", type=int, default=2,
                    help="extra attempts per repo if clone or mine fails (default 2, "
                         "so 3 total). The miner checkpoints, so a retried mine "
                         "resumes rather than redoing finished PRs.")
    ap.add_argument("--test", action="store_true",
                    help="test run: first repo only, --test-prs PRs, fresh output in "
                         "test_output/test_dataset.jsonl (readable JSON lines); clone kept")
    ap.add_argument("--test-prs", type=int, default=10)
    ap.add_argument("--test-repo", default="https://github.com/pallets/flask",
                    help="repo used by --test instead of the repos file (default: "
                         "pallets/flask, the smallest repo in the corpus)")
    args = ap.parse_args()

    if args.test:
        entries = [p for p in [_parse_url(args.test_repo)] if p]
    else:
        if not os.path.isfile(args.repos_file):
            sys.exit(f"repos file not found: {args.repos_file}")
        with open(args.repos_file, encoding="utf-8") as fh:
            entries = [p for p in (_parse_url(line) for line in fh) if p]
    if not entries:
        sys.exit("no valid GitHub URLs found in the repos file")
    if args.test:
        args.max_prs = args.test_prs
        args.skip_existing = True            # reuse the test clone between runs
        args.out = TEST_OUT
        args.keep_clones = True
        os.makedirs(os.path.dirname(TEST_OUT), exist_ok=True)
        for f in (TEST_OUT, TEST_OUT + ".partial.jsonl", TEST_OUT.rsplit(".", 1)[0] + ".parquet"):
            if os.path.isfile(f):
                os.remove(f)                 # fresh file every test run
        print(f"[test] {entries[0][0]}/{entries[0][1]}, {args.max_prs} PRs -> {TEST_OUT}",
              flush=True)

    os.makedirs(args.clone_dir, exist_ok=True)
    here = os.path.dirname(os.path.abspath(__file__))
    miner = os.path.join(here, "mine_repo.py")

    print(f"[batch] {len(entries)} repos -> {args.out}\n", flush=True)
    progress.emit("batch_start", repos=[f"{o}/{n}" for o, n, _ in entries], out=args.out)
    attempts = max(1, args.repo_retries + 1)
    ok, failed = 0, []
    for i, (owner, name, url) in enumerate(entries, 1):
        target = os.path.join(args.clone_dir, f"{owner}_{name}")
        print(f"=== [{i}/{len(entries)}] {owner}/{name} ===", flush=True)
        progress.emit("repo_queue", repo=f"{owner}/{name}", i=i, n=len(entries), stage="clone")

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
            progress.emit("repo_failed", repo=f"{owner}/{name}", stage="clone")
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
        if args.no_scanners:
            cmd.append("--no-scanners")
        if args.no_api_contract:
            cmd.append("--no-api-contract")
        if args.no_review_history:
            cmd.append("--no-review-history")
        if args.llm_model:
            cmd += ["--llm-model", args.llm_model]
        if args.workers is not None:
            cmd += ["--workers", str(args.workers)]
        if args.llm_concurrency is not None:
            cmd += ["--llm-concurrency", str(args.llm_concurrency)]

        mined = False
        progress.emit("repo_queue", repo=f"{owner}/{name}", i=i, n=len(entries), stage="mine")
        for m_attempt in range(1, attempts + 1):
            rc = subprocess.run(cmd).returncode
            if rc == 3:
                break                       # LLM circuit breaker: don't retry
            if rc == 0:
                ok += 1
                mined = True
                break
            print(f"[mine] FAILED (exit {rc}) attempt {m_attempt}/{attempts}",
                  flush=True)
            if m_attempt < attempts:
                time.sleep(min(120, 15 * m_attempt))
        if rc == 3:
            print("[mine] LLM circuit breaker tripped; aborting the batch so the "
                  "dataset isn't left with half-populated LLM columns. Fix the LLM, "
                  "then rerun with --skip-existing to resume.", flush=True)
            failed.append(f"{owner}/{name} (llm breaker)")
            break
        if not mined:
            print("[mine] giving up after retries; moving on", flush=True)
            failed.append(f"{owner}/{name} (mine)")
            progress.emit("repo_failed", repo=f"{owner}/{name}", stage="mine")

        # delete the clone before the next repo so disk never piles up (one clone
        # on disk at a time). Mined rows are already in --out; per-repo caches
        # (ci_cache/) and the .partial.jsonl checkpoint are kept. --keep-clones
        # opts out; so does --skip-existing (which exists to reuse clones).
        if not args.keep_clones and not args.skip_existing and os.path.isdir(target):
            print(f"[clone] removing {target} (mining done)", flush=True)
            _rmtree(target)
        print(flush=True)

    if args.test and os.path.isfile(TEST_OUT):
        _write_test_parquet(TEST_OUT)
    print(f"[batch] done: {ok}/{len(entries)} repos mined -> {args.out}")
    progress.emit("batch_done", ok=ok, n=len(entries), failed=failed)
    if failed:
        print("[batch] failures:")
        for f in failed:
            print("  -", f)


if __name__ == "__main__":
    main()
