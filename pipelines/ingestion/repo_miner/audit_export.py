#!/usr/bin/env python3
"""
audit_export.py — a sample for the human label check (proposal Part 4).

Draws a stratified sample of mined PRs, about the same number per outcome
class, with agent PRs over-weighted (v0.4's key slice), and writes a CSV with
GitHub links, the miner's labels, and blank columns for the human verdict.
Comparing the two gives the precision of each label (label_unwanted and
label_duplicate especially) before training on them.

Classes: merged clean, merged after rework, and unmerged by close_reason
(duplicate, unwanted, ci_technical, abandoned, other).

Usage:
  python audit_export.py --dataset dataset.parquet --out label_audit.csv [--n 600]
"""
from __future__ import annotations

import argparse
import csv
import random

from snapshots import _read_rows

CLASSES = ("merged_clean", "merged_rework", "duplicate", "unwanted", "ci_technical",
           "abandoned", "other")
COLUMNS = ("stratum", "repo", "pr_number", "url", "ai_agent_authored", "agent_name",
           "author_association", "label_merged", "label_rework", "close_reason",
           "close_reason_source", "label_duplicate", "label_unwanted", "label_abandoned",
           "label_ci_technical", "human_outcome", "human_reason", "notes")


def stratum(r: dict) -> str | None:
    if r.get("label_merged") is True:
        return "merged_rework" if r.get("label_rework") else "merged_clean"
    if r.get("label_merged") is False:
        return r.get("close_reason") if r.get("close_reason") in CLASSES else "other"
    return None


def draw(rows: list[dict], n: int, seed: int, agent_share: float = 0.5) -> list[dict]:
    """About n / len(CLASSES) per class; within a class up to agent_share of
    the picks are agent PRs. Small classes give what they have."""
    rnd = random.Random(seed)
    per = max(1, n // len(CLASSES))
    out = []
    for c in CLASSES:
        pool = sorted((r for r in rows if stratum(r) == c), key=lambda r: (r["repo"], r["pr_number"]))
        agents = [r for r in pool if r.get("ai_agent_authored")]
        others = [r for r in pool if not r.get("ai_agent_authored")]
        rnd.shuffle(agents)
        rnd.shuffle(others)
        take_a = min(len(agents), int(per * agent_share))
        picked = agents[:take_a] + others[: per - take_a]
        if len(picked) < per:
            picked += agents[take_a: take_a + per - len(picked)]
        out += [dict(r, stratum=c) for r in picked]
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--out", default="label_audit.csv")
    ap.add_argument("--n", type=int, default=600)
    ap.add_argument("--seed", type=int, default=5)
    args = ap.parse_args()
    picked = draw(_read_rows(args.dataset), args.n, args.seed)
    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in picked:
            w.writerow({**r, "url": f"https://github.com/{r['repo']}/pull/{r['pr_number']}",
                        "human_outcome": "", "human_reason": "", "notes": ""})
    counts = {c: sum(1 for r in picked if r["stratum"] == c) for c in CLASSES}
    print(f"[audit] {len(picked)} PRs -> {args.out}: {counts}")


if __name__ == "__main__":
    main()
