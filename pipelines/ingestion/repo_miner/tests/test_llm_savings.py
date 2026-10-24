"""LLM plan-quota savings: diff slimming, scope, trivial skips, rules-first
close reasons, waiting out plan limits, RAM-aware workers, clean stops."""
from __future__ import annotations

import os
import stat
import subprocess
import sys
import time
import time as _time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import build_dataset  # noqa: E402
import config  # noqa: E402
import mine_repo  # noqa: E402
from features import llm_flags as L  # noqa: E402


def _file(path, body):
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n{body}"


# ---- diff slimming -----------------------------------------------------------

def test_slim_keeps_code_and_notes_lockfiles_and_generated():
    diff = (_file("src/app.ts", "-a\n+b\n") + _file("pnpm-lock.yaml", "+x\n" * 500)
            + _file("dist/bundle.min.js", "+y\n" * 50))
    out = L.slim_diff(diff, 25000)
    assert "src/app.ts" in out and "+b" in out
    assert "# pnpm-lock.yaml: lockfile, +500 -0 lines (diff omitted)" in out
    assert "dist/bundle.min.js: generated" in out
    assert "+x" not in out and "+y" not in out


def test_slim_cap_drops_whole_files_and_says_so():
    diff = _file("a.py", "+1\n" * 100) + _file("b.py", "+2\n" * 5000)
    out = L.slim_diff(diff, 1000)
    assert "a.py" in out and "+2" not in out
    assert "1 more changed file(s) not shown (size cap): b.py" in out


def test_slim_truncates_a_single_huge_file():
    out = L.slim_diff(_file("a.py", "+1\n" * 5000), 500)
    assert "# (file truncated)" in out and len(out) < 700


def test_slim_leaves_small_diff_alone():
    diff = _file("a.py", "-x\n+y\n")
    assert L.slim_diff(diff, 25000) == diff


# ---- which rows get LLM calls ------------------------------------------------

def _st(**kw):
    st = config.Settings()
    for k, v in kw.items():
        setattr(st, k, v)
    return st


def _pr(assoc="CONTRIBUTOR", num=1, user_type="User"):
    return {"author_association": assoc, "number": num, "user": {"type": user_type}}


def test_scope_training_keeps_outside_contributors_only():
    st = _st(llm_sample_pct=0)
    row = {"repo": "o/r", "author_is_bot": False}
    assert mine_repo._llm_skip_reason(_pr("CONTRIBUTOR"), row, ["a.py"], st) is None
    assert mine_repo._llm_skip_reason(_pr("FIRST_TIME_CONTRIBUTOR"), row, ["a.py"], st) is None
    assert mine_repo._llm_skip_reason(_pr("MEMBER"), row, ["a.py"], st) == "scope"
    assert mine_repo._llm_skip_reason(_pr("CONTRIBUTOR"), {"repo": "o/r", "author_is_bot": True},
                                      ["a.py"], st) == "scope"
    assert mine_repo._llm_skip_reason(_pr("NONE", user_type="Bot"), row, ["a.py"], st) == "scope"
    # no API (git-only discovery): unknown association counts as outside
    assert mine_repo._llm_skip_reason(_pr(None), row, ["a.py"], st) is None
    assert mine_repo._llm_skip_reason(_pr("MEMBER"), row, ["a.py"], _st(llm_scope="all")) is None


def test_scope_sample_is_stable_and_about_the_right_size():
    st = _st(llm_sample_pct=5)
    row = {"repo": "o/r", "author_is_bot": False}
    picked = [n for n in range(4000) if mine_repo._llm_in_scope(_pr("MEMBER", n), row, st)]
    assert 120 < len(picked) < 280                       # ~5% of 4000
    assert picked == [n for n in range(4000) if mine_repo._llm_in_scope(_pr("MEMBER", n), row, st)]


def test_trivial_diffs_are_skipped():
    st = _st()
    row = {"repo": "o/r", "author_is_bot": False}
    assert mine_repo._llm_skip_reason(_pr(), dict(row, docs_only=True), ["README.md"], st) == "trivial"
    assert mine_repo._llm_skip_reason(_pr(), dict(row, test_only=True), ["a.test.ts"], st) == "trivial"
    assert mine_repo._llm_skip_reason(_pr(), row, ["pnpm-lock.yaml", "dist/x.js"], st) == "trivial"
    assert mine_repo._llm_skip_reason(_pr(), row, ["pnpm-lock.yaml", "src/a.ts"], st) is None
    assert mine_repo._llm_skip_reason(_pr(), dict(row, docs_only=True), ["README.md"],
                                      _st(llm_skip_trivial=False)) is None


def test_skipped_rows_resolve_to_null_without_touching_the_breaker():
    st = _st()
    fetched = L.fetch("/nonexistent", "a", "b", st, skip="scope")
    assert fetched["status"] == "skipped"
    assert all(v is None for v in L.resolve(fetched, st).values())


# ---- close reason: rules first -----------------------------------------------

def test_close_reason_asks_llm_only_when_rules_say_other(monkeypatch):
    calls = []
    monkeypatch.setattr(mine_repo, "classify_close", lambda pr, ev, st: calls.append(1) or "unwanted")
    monkeypatch.setattr(mine_repo, "close_transcript", lambda pr, ev: "maintainer: not planned")
    row = {"repo": "o/r", "author_is_bot": False}
    for reason, asks in (("duplicate", False), ("other", True)):
        monkeypatch.setattr(mine_repo, "build_merge_labels",
                            lambda pr, ev, ci, st, llm, r=reason: {"close_reason": r})
        calls.clear()
        got = mine_repo._close_reason_from_llm(_pr(), row, [], None, _st())
        assert (got == "unwanted") is asks and bool(calls) is asks
    monkeypatch.setattr(mine_repo, "build_merge_labels", lambda *a: {"close_reason": "duplicate"})
    assert mine_repo._close_reason_from_llm(_pr(), row, [], None, _st(close_reason_llm="always")) == "unwanted"
    assert mine_repo._close_reason_from_llm(_pr("MEMBER"), row, [], None, _st(llm_sample_pct=0)) is None


# ---- plan quota ---------------------------------------------------------------

class FakeClock:
    def __init__(self):
        self.now = 1_000_000.0
        self.slept = 0.0

    def time(self):
        return self.now

    def sleep(self, s):
        self.slept += s
        self.now += s

    def monotonic(self):
        return self.now

    strftime = staticmethod(_time.strftime)
    localtime = staticmethod(_time.localtime)


@pytest.fixture
def clock(monkeypatch):
    c = FakeClock()
    monkeypatch.setattr(L, "time", c)
    L._QUOTA.update(checked=0.0, lines=None, paused_until=0.0)
    yield c
    L._QUOTA.update(checked=0.0, lines=None, paused_until=0.0)


def test_gate_waits_for_reset_when_plan_is_nearly_used(clock, monkeypatch):
    monkeypatch.setattr(L, "claude_plan_usage", lambda: [
        {"label": "Session", "pct": 97.0, "resets_at": clock.now + 600}])
    L._quota_gate(_st(llm_backend="claude_cli"))
    assert 600 <= clock.slept <= 700


def test_gate_does_not_block_below_threshold_or_when_unreadable(clock, monkeypatch):
    monkeypatch.setattr(L, "claude_plan_usage", lambda: [
        {"label": "Session", "pct": 50.0, "resets_at": clock.now + 600}])
    L._quota_gate(_st(llm_backend="claude_cli"))
    L._QUOTA["checked"] = 0.0
    monkeypatch.setattr(L, "claude_plan_usage", lambda: None)
    L._quota_gate(_st(llm_backend="claude_cli"))
    assert clock.slept == 0


def test_limit_hit_waits_then_retries(clock, monkeypatch):
    monkeypatch.setattr(L, "claude_plan_usage", lambda: None)
    attempts = []

    def call():
        attempts.append(1)
        if len(attempts) == 1:
            raise L.QuotaHit("usage limit reached", clock.now + 1200)
        return {"ok": True}

    assert L._call_with_quota(call, _st(llm_backend="claude_cli")) == {"ok": True}
    assert len(attempts) == 2 and clock.slept >= 1200


def test_limit_resetting_too_far_away_fails_the_call(clock, monkeypatch):
    monkeypatch.setattr(L, "claude_plan_usage", lambda: None)

    def call():
        raise L.QuotaHit("weekly limit reached", clock.now + 3 * 86400)

    assert L._call_with_quota(call, _st(llm_backend="claude_cli")) is None
    assert clock.slept == 0


def test_claude_cli_limit_message_raises_quota_hit(tmp_path, monkeypatch):
    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\necho '{\"is_error\": true, \"result\": \"Claude usage limit reached. "
                    "Your limit will reset at 5pm\"}'\nexit 1\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    with pytest.raises(L.QuotaHit):
        L._call_claude_cli("diff", _st(llm_backend="claude_cli"), retries=0)


# ---- workers and stopping -------------------------------------------------------

def test_default_workers_fit_free_ram():
    assert config.default_workers(ram_gb=100, cpus=16) == 6
    assert config.default_workers(ram_gb=100, cpus=2) == 2
    assert config.default_workers(ram_gb=3.1, cpus=16) == 2
    assert config.default_workers(ram_gb=0.5, cpus=16) == 1
    assert config.default_workers(ram_gb=None, cpus=4) in range(1, 5)


def test_stop_child_ends_the_miner_quickly():
    import signal
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    t0 = time.monotonic()
    build_dataset._stop_child(proc, signal)
    assert proc.poll() is not None and time.monotonic() - t0 < 10


def test_batch_stop_prints_one_line_not_a_traceback():
    import signal
    child = subprocess.Popen(
        [sys.executable, "-c",
         "import time, build_dataset as b; b.main = lambda: time.sleep(60); b.run()"],
        cwd=os.path.dirname(build_dataset.__file__), stderr=subprocess.PIPE, text=True)
    time.sleep(2)
    child.send_signal(signal.SIGINT)
    _, err = child.communicate(timeout=30)
    assert child.returncode == 130
    assert "Traceback" not in err and "[stop] batch interrupted" in err


def test_close_reason_without_human_activity_never_asks(monkeypatch):
    monkeypatch.setattr(mine_repo, "classify_close", lambda *a: pytest.fail("asked the LLM"))
    monkeypatch.setattr(mine_repo, "build_merge_labels", lambda *a: {"close_reason": "other"})
    monkeypatch.setattr(mine_repo, "close_transcript", lambda pr, ev: None)
    assert mine_repo._close_reason_from_llm(_pr(), {"repo": "o/r"}, [], None, _st()) is None


def test_close_reason_that_stays_unanswered_stops_the_repo(monkeypatch):
    monkeypatch.setattr(mine_repo, "classify_close", lambda *a: None)
    monkeypatch.setattr(mine_repo, "build_merge_labels", lambda *a: {"close_reason": "other"})
    monkeypatch.setattr(mine_repo, "close_transcript", lambda pr, ev: "text")
    with pytest.raises(L.LLMUnavailable):
        mine_repo._close_reason_from_llm(_pr(), {"repo": "o/r"}, [], None, _st(llm_retry_backoff_s=0))



def test_quota_pause_is_counted_as_wait_not_work(monkeypatch):
    import time
    from features import llm_flags
    st = config.Settings()
    monkeypatch.setitem(llm_flags._QUOTA, "paused_until", time.time() + 0.3)
    w0 = llm_flags.quota_wait_s()
    llm_flags._wait_paused(st)
    assert 0.2 < llm_flags.quota_wait_s() - w0 < 2
    w1 = llm_flags.quota_wait_s()
    llm_flags._wait_paused(st)                 # no pause active: adds nothing
    assert llm_flags.quota_wait_s() - w1 < 0.05
