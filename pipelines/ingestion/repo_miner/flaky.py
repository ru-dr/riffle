"""
flaky.py — flaky-check detection for label_ci_fail (rules.mining.flaky_detection).

A required check that failed on a commit and then passed when re-run there is
a rerun-pass. A check is flaky at a moment when it had at least
flaky_min_rerun_passes rerun-passes in the flaky_lookback_days before it
(contract defaults 3 and 90), counted over the repo's mined merge commits. A
merge-commit CI failure that comes only from checks flaky at merge time does
not count: label_ci_fail becomes False, label_bad is recomputed, and
ci_fail_flaky_only says so.

Point-in-time: only rerun-passes strictly before a PR's merge count, so a
label never uses CI history from after its own merge. Runs once per repo,
after every row is mined (labels.py records each row's checks in a sidecar).
"""
from __future__ import annotations

import bisect
from datetime import datetime, timedelta

import config
from labels import CI_SIDECAR


def _ts(s) -> datetime | None:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")) if s else None
    except ValueError:
        return None


def apply(rows: list[dict], st: config.Settings) -> int:
    """Correct label_ci_fail / label_bad on rows whose CI failure is flaky-only.
    Returns how many rows changed."""
    if not st.flaky_detection:
        return 0
    passes: dict[str, list[datetime]] = {}
    for r in rows:
        sc = r.get(CI_SIDECAR) or {}
        at = _ts(sc.get("at"))
        for name in sc.get("rerun_passes") or []:
            if at:
                passes.setdefault(name, []).append(at)
    for v in passes.values():
        v.sort()
    lookback = timedelta(days=st.flaky_lookback_days)

    def flaky(name: str, t: datetime) -> bool:
        v = passes.get(name, [])
        return bisect.bisect_left(v, t) - bisect.bisect_left(v, t - lookback) >= st.flaky_min_rerun_passes

    changed = 0
    for r in rows:
        sc = r.get(CI_SIDECAR) or {}
        at, failing = _ts(sc.get("at")), sc.get("failing") or []
        if r.get("label_ci_fail") is not True or not at or not failing:
            continue
        if all(flaky(n, at) for n in failing):
            r["label_ci_fail"] = False
            r["ci_fail_flaky_only"] = True
            r["label_bad"] = bool(r.get("label_revert") or r.get("label_hotfix"))
            changed += 1
    return changed
