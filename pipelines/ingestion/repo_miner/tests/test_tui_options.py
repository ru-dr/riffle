"""The TUI passes the v3-v6 options to build_dataset.py and checks them."""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

os.environ.setdefault("RIFFLE_NO_DOCTOR", "1")
os.environ.setdefault("RIFFLE_NO_PLAN_USAGE", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

textual = pytest.importorskip("textual")
from textual.widgets import Checkbox, Input  # noqa: E402

import tui  # noqa: E402


def _run(fn):
    async def go():
        app = tui.RiffleTUI()
        async with app.run_test(size=(160, 60)) as pilot:
            await pilot.pause()
            return fn(app)
    return asyncio.run(go())


def _set(app, **vals):
    for wid, v in vals.items():
        w = app.query_one(f"#{wid}")
        if isinstance(w, Checkbox):
            w.value = v
        elif isinstance(w, Input):
            w.value = v


def test_defaults_pass_the_new_options():
    cmd = _run(lambda app: app._build_cmd())
    assert ["--llm-scope", "training"] == cmd[cmd.index("--llm-scope"):cmd.index("--llm-scope") + 2]
    assert "--close-reason-llm" in cmd and "other_only" in cmd
    assert "--llm-quota-wait" in cmd and "--snapshots" in cmd
    assert "--window-from" not in cmd                        # newest-N mode until dates are set


def test_window_cap_lead_in_and_domain():
    def fn(app):
        _set(app, window_from="2023-07-01", window_to="2026-07-31", cap_training=True,
             lead_in="90", only_domain="web-ts", llm_training=False, snapshots=False,
             quota_wait=False, close_other_only=False)
        return app._build_cmd()
    cmd = _run(fn)
    i = cmd.index("--window-from")
    assert cmd[i:i + 4] == ["--window-from", "2023-07-01", "--window-to", "2026-07-31"]
    assert cmd[cmd.index("--cap-on") + 1] == "training"
    assert cmd[cmd.index("--lead-in-days") + 1] == "90"
    assert cmd[cmd.index("--only-domain") + 1] == "web-ts"
    assert cmd[cmd.index("--llm-scope") + 1] == "all" and cmd[cmd.index("--close-reason-llm") + 1] == "always"
    assert "--no-snapshots" in cmd and "--no-llm-quota-wait" in cmd


@pytest.mark.parametrize("vals, ok", [
    ({}, True),
    ({"window_from": "2023-07-01", "window_to": "2026-07-31"}, True),
    ({"window_from": "2023-07-01"}, False),                       # one date only
    ({"window_from": "2026-07-31", "window_to": "2023-07-01"}, False),  # backwards
    ({"window_from": "07/01/2023", "window_to": "2026-07-31"}, False),  # format
    ({"cap_training": True}, False),                               # cap needs a window
])
def test_window_validation(vals, ok):
    def fn(app):
        _set(app, **vals)
        return app._window_ok()
    assert _run(fn) is ok


def test_build_dataset_accepts_every_flag_the_tui_sends():
    import subprocess
    cmd = _run(lambda app: (_set(app, window_from="2023-07-01", window_to="2026-07-31",
                                 cap_training=True, only_domain="web-ts"), app._build_cmd())[1])
    # --shard-plan makes build_dataset parse every flag, print a plan, and exit without mining
    p = subprocess.run([*cmd, "--shard-plan", "1"], cwd=tui.HERE, capture_output=True, text=True, timeout=60)
    assert "unrecognized arguments" not in p.stderr and p.returncode == 0, p.stderr[-800:]


def test_status_shows_skips_and_plan_pause():
    import time

    def fn(app):
        published = []
        app._publish = lambda summary=None: published.append(summary)
        app.proc = type("P", (), {"pid": os.getpid()})()      # pretend a run is going
        app.stats.started = time.time() - 60
        now = time.time()
        app._apply({"ev": "repo_start", "repo": "o/r", "total": 3, "t": now})
        for status in ("ok", "skipped", "skipped"):
            app._apply({"ev": "pr", "repo": "o/r", "status": "ok", "llm": status, "secs": 1.0, "t": now})
        app._apply({"ev": "llm_quota", "until": now + 1800, "why": "plan Session at 97%", "t": now})
        app._render_stats()
        app.proc = None
        text = str(app.query_one("#stats").render())
        return text, published[-1]
    text, summary = _run(fn)
    assert "LLM ok 1  fail 0  skipped 2" in text
    assert "LLM paused for plan reset until" in text
    assert summary["llm_skipped"] == 2 and summary["llm_paused"].startswith("LLM paused for plan reset")


def test_full_repo_drops_the_cap_and_window():
    def fn(app):
        _set(app, full_repo=True, max_prs="100", window_from="2023-07-01", window_to="2026-07-31")
        return app._build_cmd()
    cmd = _run(fn)
    assert "--full-repo" in cmd
    assert "--max-prs" not in cmd and "--window-from" not in cmd


def test_a_repo_cut_off_by_stop_is_not_shown_as_mining():
    def check(app):
        r = tui.RepoStat(name="a/b", stage="mining", total=100, done=40, started=1000.0, last_event=1000.0)
        r.first_pr, r.resumed = 1010.0, 0
        app._final_state = "stopped"
        assert "mining" in app._row_values(r, 5000.0)["stage"]          # while it runs, the stall is shown
        r.halted = 2000.0
        v = app._row_values(r, 5000.0)
        assert v["stage"] == "stopped at 40/100"
        assert v["eta"] == "—"                                          # no ETA for a stopped repo
        assert v["time"] == tui._fmt_secs(1000.0)                       # its clock stopped when the batch did
    _run(check)
