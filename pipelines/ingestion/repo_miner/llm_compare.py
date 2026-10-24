#!/usr/bin/env python3
"""
llm_compare.py — do two LLM models agree on the 23 flags? (proposal 1.7)

Before the big run, pick ONE model for the whole dataset. This asks two
models (default: sonnet and haiku, through headless Claude Code) about the
same diffs, sent exactly as the miner sends them (llm_flags.llm_diff, the same
prompt and schema, the same plan-quota pause), and reports per-flag agreement.

Samples in-scope rows (outside contributors, not bots, not trivial diffs) from
a mined dataset whose repos are still cloned. Answers go to --answers as they
arrive, so a stopped run resumes.

Usage:
  python llm_compare.py --dataset dataset.parquet --clone-dir cloned_repos \
      --models sonnet haiku --n 300 [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from collections import Counter

import config
import snapshot_capture as cap
from features import llm_flags
from snapshots import _read_rows

OUTSIDE = {"CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR", "FIRST_TIMER", "NONE"}


def _repo_key(repo) -> str:
    """owner/name however the dataset spelled the repo (URL, .git, trailing slash)."""
    r = str(repo or "").strip().rstrip("/")
    if r.endswith(".git"):
        r = r[:-4]
    parts = [x for x in r.replace("\\", "/").split("/") if x]
    return "/".join(parts[-2:]) if len(parts) >= 2 else r


def sample(rows: list[dict], clone_dir: str, n: int, seed: int) -> list[dict]:
    """In-scope rows whose repo is cloned, a fixed random sample of n."""
    ok = []
    for r in rows:
        key = _repo_key(r.get("repo"))
        clone = os.path.join(clone_dir, key.replace("/", "_"))
        # an unknown association (no API) counts as outside, as in the miner's scope rule
        if ((r.get("author_association") is None or r.get("author_association") in OUTSIDE)
                and not r.get("author_is_bot")
                and not r.get("author_confirmed_maintainer")
                and not r.get("docs_only") and not r.get("test_only")
                and r.get("base_sha") and r.get("head_sha") and os.path.isdir(clone)):
            ok.append(dict(r, repo=key, _clone=clone))
    ok.sort(key=lambda r: (r["repo"], r["pr_number"]))
    random.Random(seed).shuffle(ok)
    return ok[:n]


def agreement(answers: dict[str, dict[str, dict]], a: str, b: str) -> dict:
    """{flag: share of PRs where both models gave the same non-null value},
    plus "overall" (all flags pooled, so flags answered more often weigh more),
    "macro" (the mean of the per-flag shares) and "pairs" (PRs answered by both)."""
    keys = [k for k in answers.get(a, {}) if k in answers.get(b, {})]
    out, total, same_total = {}, 0, 0
    for flag in llm_flags.FLAG_SCHEMA:
        pairs = [(answers[a][k].get(flag), answers[b][k].get(flag)) for k in keys]
        pairs = [(x, y) for x, y in pairs if x is not None and y is not None]
        same = sum(1 for x, y in pairs if x == y)
        out[flag] = round(same / len(pairs), 3) if pairs else None
        total += len(pairs)
        same_total += same
    out["overall"] = round(same_total / total, 3) if total else None
    shares = [out[f] for f in llm_flags.FLAG_SCHEMA if out[f] is not None]
    out["macro"] = round(sum(shares) / len(shares), 3) if shares else None
    out["pairs"] = len(keys)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--clone-dir", default="cloned_repos")
    ap.add_argument("--models", nargs=2, default=["sonnet", "haiku"])
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--seed", type=int, default=13)
    ap.add_argument("--answers", default="llm_compare_answers.jsonl")
    ap.add_argument("--dry-run", action="store_true", help="show the sample size and calls, call nothing")
    args = ap.parse_args()

    rows = sample(_read_rows(args.dataset), args.clone_dir, args.n, args.seed)
    # an answer of null (the call failed earlier) is asked again on a rerun
    done = {(r["model"], r["key"]): r["flags"] for r in cap.read_jsonl(args.answers) if r.get("flags")}
    todo = [(m, r) for m in args.models for r in rows
            if (m, f"{r['repo']}#{r['pr_number']}") not in done]
    print(f"[compare] {len(rows)} PRs sampled; {len(todo)} calls to make "
          f"({len(done)} answers already in {args.answers})", file=sys.stderr)
    if args.dry_run:
        return
    st = config.Settings()
    st.llm_backend = "claude_cli"
    with open(args.answers, "a", encoding="utf-8") as fh:
        for i, (model, r) in enumerate(todo, 1):
            st.llm_model = model
            diff, _truncated, has_code = llm_flags.llm_diff_info(r["_clone"], r["base_sha"], r["head_sha"], st)
            if not diff.strip() or not has_code:
                continue
            flags = None
            for _ in range(3):                  # a failed call is retried, not recorded as an answer
                raw = llm_flags._call_with_quota(lambda: llm_flags._call_claude_cli(diff, st), st)
                got = llm_flags._validate(raw, diff) if isinstance(raw, dict) else None
                if got and any(v is not None for v in got.values()):
                    flags = got
                    break
            if flags is None:
                print(f"[compare] {model} gave no usable answer for {r['repo']}#{r['pr_number']}; "
                      "it is asked again on the next run", file=sys.stderr)
                continue
            key = f"{r['repo']}#{r['pr_number']}"
            done[(model, key)] = flags
            fh.write(json.dumps({"model": model, "key": key, "flags": flags}) + "\n")
            fh.flush()
            if i % 25 == 0:
                print(f"[compare] {i}/{len(todo)} · {llm_flags.usage_summary()}", file=sys.stderr)
    answers: dict[str, dict] = {}
    for (model, key), flags in done.items():
        if flags:
            answers.setdefault(model, {})[key] = flags
    a, b = args.models
    rep = agreement(answers, a, b)
    print(f"\n{a} vs {b}: {rep['pairs']} PRs answered by both; overall agreement {rep['overall']} "
          f"(macro mean over flags {rep['macro']})")
    for flag in sorted(llm_flags.FLAG_SCHEMA, key=lambda f: (rep[f] is None, rep[f] or 0)):
        print(f"  {flag:36} {rep[flag]}")
    print("\nFlags below ~0.8 disagree often; check them before switching models.")


if __name__ == "__main__":
    main()
