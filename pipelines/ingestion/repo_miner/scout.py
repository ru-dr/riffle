#!/usr/bin/env python3
"""
scout.py — metadata-only repo scouting, no clone (proposal Part 3).

Ranks candidate repos for a domain by what the merge model needs: outside
contributors, unmerged outside PRs (so both outcomes exist), few bot PRs, and
agent PRs. One API call per repo (the 100 most recently created closed PRs);
--agent-search adds three search calls per repo for agent PR counts over the
last 12 months (search is limited to 30 calls a minute).

Usage:
  python scout.py --repos-file repos_web_ts.txt [--agent-search] [--out scout.json]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone

import config
from build_dataset import _parse_domain, _parse_url
from features import merge_signals
from github_api import GitHubAPI
from health import MAX_BOT_SHARE, MIN_OUTSIDE_UNMERGED, OUTSIDE

AGENT_QUERIES = {"copilot": "author:app/copilot-swe-agent", "codex": "head:codex/",
                 "claude": '"Generated with Claude Code" in:body'}


def _is_bot(pr: dict) -> bool:
    u = pr.get("user") or {}
    return u.get("type") == "Bot" or str(u.get("login") or "").lower().endswith("[bot]")


def stats(prs: list[dict]) -> dict:
    """Shares over a sample of closed PRs (the PR-list objects)."""
    n = len(prs)
    agents = [p for p in prs if merge_signals.agent_name((p.get("user") or {}).get("login"),
                                                         (p.get("head") or {}).get("ref"), None, p.get("body"))]
    bots = [p for p in prs if _is_bot(p) and p not in agents]
    outside = [p for p in prs if p not in bots and p.get("author_association") in OUTSIDE]
    out_unmerged = [p for p in outside if not p.get("merged_at")]
    share = lambda a, b: round(a / b, 3) if b else None  # noqa: E731
    s = {"sample": n, "outside_share": share(len(outside), n),
         "outside_unmerged_share": share(len(out_unmerged), len(outside)),
         "bot_share": share(len(bots), n), "agent_share": share(len(agents), n),
         "merged_share": share(sum(1 for p in prs if p.get("merged_at")), n)}
    flags = []
    if n and (s["bot_share"] or 0) > MAX_BOT_SHARE:
        flags.append("mostly bot PRs")
    if outside and (s["outside_unmerged_share"] or 0) < MIN_OUTSIDE_UNMERGED:
        flags.append("almost no unmerged outside PRs")
    if n and (s["outside_share"] or 0) < 0.15:
        flags.append("few outside contributors")
    s["flags"] = flags
    # rough usefulness for the merge model: outside PRs with both outcomes
    s["score"] = round((s["outside_share"] or 0) * min(1.0, 2 * (s["outside_unmerged_share"] or 0))
                       * (1 - (s["bot_share"] or 0)), 3)
    return s


def agent_counts(api: GitHubAPI, days: int = 365) -> dict:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    out = {}
    for name, q in AGENT_QUERIES.items():
        status, data, _ = api._get("/search/issues", {
            "q": f"repo:{api.owner}/{api.repo} is:pr created:>={since} {q}", "per_page": 1})
        out[name] = (data or {}).get("total_count") if status == 200 else None
        time.sleep(2.2)                       # search: 30 calls a minute
    return out


def scout(entries, st: config.Settings, agent_search: bool = False) -> list[dict]:
    res = []
    for owner, name, domain in entries:
        api = GitHubAPI(owner, name, st)
        status, data, _ = api._get(f"/repos/{owner}/{name}/pulls",
                                   {"state": "closed", "per_page": 100, "sort": "created",
                                    "direction": "desc"})
        row = {"repo": f"{owner}/{name}", "domain": domain}
        if status != 200 or not isinstance(data, list):
            row["error"] = f"PR list status {status}"
        else:
            row.update(stats(data))
            if agent_search:
                row["agent_prs_12m"] = agent_counts(api)
        res.append(row)
        print(json.dumps(row), file=sys.stderr, flush=True)
    return sorted(res, key=lambda r: r["score"] if r.get("score") is not None else -1, reverse=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repos-file", default="repos_web_ts.txt")
    ap.add_argument("--agent-search", action="store_true")
    ap.add_argument("--out", default=None, help="write the ranked results as JSON")
    args = ap.parse_args()
    entries = []
    with open(args.repos_file, encoding="utf-8") as fh:
        for line in fh:
            p = _parse_url(line)
            if p:
                entries.append((p[0], p[1], _parse_domain(line)))
    res = scout(entries, config.Settings(), args.agent_search)
    pct = lambda v: "   -" if v is None else f"{v:4.0%}"  # noqa: E731
    print(f"{'repo':40} score outside unmerged bots agents  flags")
    for r in res:
        if "error" in r:
            print(f"{r['repo']:40} {r['error']}")
            continue
        print(f"{r['repo']:40} {r['score']:5.2f}  {pct(r['outside_share'])}    {pct(r['outside_unmerged_share'])} "
              f"{pct(r['bot_share'])}  {pct(r['agent_share'])}  {', '.join(r['flags'])}")
    if args.out:
        with open(args.out, "w") as fh:
            json.dump(res, fh, indent=2)


if __name__ == "__main__":
    main()
