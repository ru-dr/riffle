#!/usr/bin/env python3
"""
trace_report.py — summarize a run trace (logs/run-*.trace.jsonl, see runlog.py).

    python trace_report.py logs/run-20261019-225216.trace.jsonl [--top 10]

Prints how long each run stage took, the slowest PRs with their time split by
stage, quota waits, every WARN/ERROR grouped by message, and the longest quiet
gaps between events, so a slow or stalled run can be explained without reading
the whole log.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime


def _ts(s: str) -> float:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%fZ").timestamp()


def _fmt(secs: float) -> str:
    m, s = divmod(int(round(secs)), 60)
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s" if m else f"{s}s"


def load(path: str) -> list[dict]:
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass                                   # a half-written last line
    return out


def report(events: list[dict], top: int = 10, out=sys.stdout) -> None:
    p = lambda *a: print(*a, file=out)                 # noqa: E731
    if not events:
        p("empty trace")
        return
    t0, t1 = _ts(events[0]["t"]), _ts(events[-1]["t"])
    p(f"{len(events)} events over {_fmt(t1 - t0)}  ({events[0]['t']} -> {events[-1]['t']})")

    p("\nStages")
    for e in events:
        if e["stage"] == "stage" and e["msg"] == "done":
            p(f"  {e['repo'] if 'repo' in e else '':<24}{e['name']:<20}{_fmt(e['dur_s']):>10}")

    prs = [e for e in events if e["stage"] == "pr" and e["msg"] == "done"]
    if prs:
        durs = sorted(e["dur_s"] for e in prs)
        p(f"\nPRs: {len(prs)} mined, median {durs[len(durs) // 2]:.1f}s, p95 "
          f"{durs[int(len(durs) * .95)]:.1f}s, max {durs[-1]:.1f}s; "
          f"quota wait inside PRs {_fmt(sum(e.get('quota_wait_s', 0) for e in prs))}")
        p(f"Slowest {top} (work time, quota waits excluded):")
        for e in sorted(prs, key=lambda e: e["dur_s"] - e.get("quota_wait_s", 0), reverse=True)[:top]:
            st = ", ".join(f"{k} {v['s']:.0f}s×{v['n']}" for k, v in list(e.get("stages", {}).items())[:4])
            p(f"  #{e.get('pr')}  {e['dur_s']:.0f}s (+{e.get('quota_wait_s', 0):.0f}s quota)  {st}")
        agg: dict = defaultdict(float)
        for e in prs:
            for k, v in e.get("stages", {}).items():
                agg[k] += v["s"]
        p("Worker time by stage (inclusive; nested stages overlap):")
        for k, v in sorted(agg.items(), key=lambda kv: -kv[1])[:12]:
            p(f"  {k:<22}{_fmt(v):>10}")

    bad = Counter((e["lvl"], e["stage"], e["msg"]) for e in events if e["lvl"] in ("WARN", "ERROR"))
    if bad:
        p("\nWarnings and errors")
        for (lvl, stage, msg), n in bad.most_common(15):
            p(f"  {n:>5}  {lvl:<5} {stage}: {msg}")
    stalls = [e for e in events if e["stage"] == "heartbeat" and e["lvl"] == "WARN"]
    if stalls:
        p(f"\nStalls ({len(stalls)} heartbeats saw no PR finish):")
        for e in stalls[:top]:
            fl = ", ".join(f"#{a['pr']} in {a['stage']} {a['for_s']}s" for a in e.get("in_flight", [])[:3])
            p(f"  {e['t']}  {e['msg']}; {fl}")

    gaps = sorted(((_ts(b["t"]) - _ts(a["t"]), a["t"]) for a, b in zip(events, events[1:])), reverse=True)[:5]
    p("\nLongest quiet gaps between events")
    for g, at in gaps:
        p(f"  {_fmt(g):>9}  after {at}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("trace")
    ap.add_argument("--top", type=int, default=10)
    a = ap.parse_args()
    report(load(a.trace), a.top)
