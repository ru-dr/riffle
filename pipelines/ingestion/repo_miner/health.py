"""
health.py — a per-repo check after mining (proposal Part 3).

Summarises the rows a repo produced and flags repos that look wrong before
they reach training: almost no unmerged outside PRs (PRs probably land
outside the merge button), mostly bot PRs, too few rows, LLM columns missing
on rows in LLM scope, or CI labels mostly unobserved.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter

from features.llm_flags import FLAG_SCHEMA

OUTSIDE = {"CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR", "FIRST_TIMER", "NONE"}

MIN_ROWS = 50
MIN_OUTSIDE_UNMERGED = 0.05
MAX_BOT_SHARE = 0.5
MAX_LLM_MISSING = 0.02
MAX_CI_UNOBSERVED = 0.8


def _share(n: int, d: int) -> float | None:
    return round(n / d, 3) if d else None


def check(rows: list[dict], in_llm_scope=None) -> dict:
    """Stats and flags for one repo's rows. `in_llm_scope(row)` says whether a
    row should have LLM columns (omit to skip that check)."""
    n = len(rows)
    merged = [r for r in rows if r.get("label_merged") is True or (r.get("merged_at") and r.get("label_merged") is None)]
    outside = [r for r in rows if r.get("author_association") in OUTSIDE and not r.get("author_is_bot")
               and not r.get("author_confirmed_maintainer")]
    outside_unmerged = [r for r in outside if r.get("label_merged") is False]
    bots = [r for r in rows if r.get("author_is_bot")]
    agents = [r for r in rows if r.get("ai_agent_authored")]
    merged_rows = [r for r in rows if r.get("label_merged") is not False]
    stats = {
        "rows": n,
        "merged_share": _share(len(merged), n),
        "outside_share": _share(len(outside), n),
        "outside_unmerged_share": _share(len(outside_unmerged), len(outside)),
        "bot_share": _share(len(bots), n),
        "agent_share": _share(len(agents), n),
        "close_reasons": dict(Counter(r.get("close_reason") for r in rows if r.get("label_merged") is False)),
        "label_bad_rate": _share(sum(1 for r in merged_rows if r.get("label_bad") is True), len(merged_rows)),
        "ci_unobserved_share": _share(sum(1 for r in merged_rows if r.get("label_ci_fail") is None),
                                      len(merged_rows)),
        "open_snapshot": dict(Counter(r.get("open_snapshot") for r in rows)),
    }
    llm_rows = [r for r in rows if in_llm_scope(r)] if in_llm_scope else []
    if in_llm_scope:
        missing = sum(1 for r in llm_rows if all(r.get(k) is None for k in FLAG_SCHEMA))
        stats["llm_rows"] = len(llm_rows)
        stats["llm_missing_share"] = _share(missing, len(llm_rows))
    flags = []
    if n < MIN_ROWS:
        flags.append(f"only {n} rows (< {MIN_ROWS})")
    if outside and (stats["outside_unmerged_share"] or 0) < MIN_OUTSIDE_UNMERGED:
        flags.append("almost no unmerged outside PRs: PRs may land outside the merge button")
    if (stats["bot_share"] or 0) > MAX_BOT_SHARE:
        flags.append(f"bot PRs are {stats['bot_share']:.0%} of rows")
    if in_llm_scope and (stats.get("llm_missing_share") or 0) > MAX_LLM_MISSING:
        flags.append(f"LLM columns missing on {stats['llm_missing_share']:.0%} of in-scope rows")
    if merged_rows and (stats["ci_unobserved_share"] or 0) > MAX_CI_UNOBSERVED:
        flags.append(f"CI unobserved on {stats['ci_unobserved_share']:.0%} of merged rows "
                     f"(check retention is ~90 days)")
    return {"stats": stats, "flags": flags}


def report(rows: list[dict], repo_key: str, directory: str = "health", in_llm_scope=None) -> dict:
    """Print a one-line summary plus any flags, and save the JSON."""
    h = check(rows, in_llm_scope)
    s = h["stats"]
    pct = lambda v: "-" if v is None else f"{v:.0%}"  # noqa: E731
    print(f"[health] {repo_key}: {s['rows']} rows · merged {pct(s['merged_share'])} · outside "
          f"{pct(s['outside_share'])} (unmerged {pct(s['outside_unmerged_share'])}) · bots "
          f"{pct(s['bot_share'])} · agents {pct(s['agent_share'])}", file=sys.stderr)
    for f in h["flags"]:
        print(f"[health] {repo_key}: FLAG {f}", file=sys.stderr)
    try:
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, repo_key.replace("/", "_") + ".json"), "w") as fh:
            json.dump({"repo": repo_key, **h}, fh, indent=2, default=str)
    except OSError:
        pass
    return h
