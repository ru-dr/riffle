"""The batch loop's upload beats: quick beats send the logs only, the tables go on their own slower cadence, a store that
fails is asked less and less often, and a Stop gives the mirror only a moment."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import build_dataset as bd  # noqa: E402
import sync  # noqa: E402


class Args:
    out = "d.parquet"
    health_dir = "health"


def _setup(monkeypatch, result):
    clock = [1000.0]
    calls = {"status": 0, "sync": []}
    monkeypatch.setattr(bd.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(bd, "_SYNC", {"args": Args, "repos": {"a/b": {"state": "mining"}}, "counts": {}})
    monkeypatch.setattr(bd, "_sync_status", lambda why: {"reason": why, "split_id": "s"})
    monkeypatch.setattr(bd, "_sync_prefix", lambda status: "7.0.2/s")
    monkeypatch.setattr(bd, "HEARTBEAT_S", 10.0)
    monkeypatch.setattr(bd, "LOG_BEAT_S", 10.0)
    monkeypatch.setattr(sync, "mirror_settings", lambda: None)
    monkeypatch.setattr(sync, "report", lambda *a, **k: None)
    monkeypatch.setattr(sync, "put_status", lambda *a, **k: calls.__setitem__("status", calls["status"] + 1) or True)
    monkeypatch.setattr(sync, "sync", lambda *a, **k: calls["sync"].append(k.get("only_logs")) or dict(result))
    return clock, calls


def test_quick_beats_send_logs_only_and_the_tables_wait_for_their_own_cadence(monkeypatch):
    clock, calls = _setup(monkeypatch, {"sent": 1, "skipped": 0, "failed": [], "stopped": None})
    bd._sync_beat(force=True)                       # the first beat is a full one
    clock[0] += 11
    bd._sync_beat()
    clock[0] += 11
    bd._sync_beat()
    assert calls["sync"] == [False, True, True]
    clock[0] += bd.FULL_BEAT_S
    bd._sync_beat()
    assert calls["sync"][-1] is False               # and a full one again after FULL_BEAT_S


def test_a_failing_store_is_asked_less_and_less_often(monkeypatch):
    clock, calls = _setup(monkeypatch, {"sent": 0, "skipped": 0, "failed": ["x (HTTP 500)"], "stopped": "store unreachable"})
    bd._sync_beat(force=True)
    first = bd._SYNC["slow"]
    assert first >= 20
    clock[0] += 11
    n = len(calls["sync"])
    bd._sync_beat()
    assert len(calls["sync"]) == n                  # too soon after a failure: not asked
    clock[0] += first + 1
    bd._sync_beat()
    assert bd._SYNC["slow"] > first and bd._SYNC["slow"] <= 300
    for _ in range(10):
        clock[0] += 400
        bd._sync_beat()
    assert bd._SYNC["slow"] == 300                  # never worse than every five minutes


def test_the_mirror_gets_a_short_budget_on_a_stop(monkeypatch):
    seen = []
    monkeypatch.setattr(bd, "_SYNC", {"args": Args, "repos": {}, "counts": {}})
    monkeypatch.setattr(bd, "_sync_prefix", lambda status: "p")
    monkeypatch.setattr(sync, "mirror_settings", lambda: ("http://127.0.0.1:1", "t"))
    monkeypatch.setattr(sync, "sync", lambda *a, **k: seen.append(k.get("budget_s")) or {})
    bd._mirror({"reason": "stopped"}, files=True, budget_s=10)
    bd._mirror({"reason": "heartbeat"}, files=True)
    assert seen == [10, 60]
