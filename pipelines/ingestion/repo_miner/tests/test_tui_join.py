"""The volunteer's path through the TUI: paste the join code, the form takes the lead's flags, a start that
would leave the split is stopped with the reasons, and uploads show on the stats panel."""
from __future__ import annotations

import asyncio
import http.server
import json
import os
import sys
import threading

import pytest

os.environ.setdefault("RIFFLE_NO_DOCTOR", "1")
os.environ.setdefault("RIFFLE_NO_PLAN_USAGE", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("textual")
from textual.widgets import Checkbox, Input, Static  # noqa: E402

import join  # noqa: E402
import tui  # noqa: E402

LEAD = ["--repos-file", "repos_web_ts.txt", "--only-domain", "web-ts", "--window-from", "2023-07-01",
        "--window-to", "2026-07-31", "--max-prs", "8000", "--cap-on", "training", "--no-llm", "--no-szz"]


@pytest.fixture
def store(monkeypatch):
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            ok = self.headers.get("Authorization") == "Bearer rw_good"
            body = json.dumps({"kind": "pcs", "name": "pc2of3-abc12345", "split": "abc12345"} if ok else {"error": "no"}).encode()
            self.send_response(200 if ok else 401)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("RIFFLE_SYNC_URL", "")          # registered so the TUI's own changes are undone afterwards
    monkeypatch.setenv("RIFFLE_SYNC_TOKEN", "")
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def _run(coro):
    async def go():
        app = tui.RiffleTUI()
        async with app.run_test(size=(170, 70)) as pilot:
            await pilot.pause()
            return await coro(app, pilot)
    return asyncio.run(go())


async def _join(app, pilot, code):
    app.query_one("#join_code", Input).value = code
    app.action_join()
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


def _text(app, wid):
    return str(app.query_one(f"#{wid}", Static).render())


def test_form_values_follow_the_lead_flags():
    v = tui.form_values(LEAD + ["--shard", "2/3"])
    assert (v["pcs"], v["pc_index"], v["max_prs"], v["window_from"], v["only_domain"]) == ("3", "2", "8000", "2023-07-01", "web-ts")
    assert v["cap_training"] is True and v["llm"] is False and v["szz"] is False and v["merge_outcome"] is True
    assert tui.form_values(["--full-repo"])["max_prs"] == ""


def test_joining_fills_the_form_saves_env_and_hides_the_code(store, tmp_path, monkeypatch):
    monkeypatch.setattr(tui, "HERE", str(tmp_path))
    code = join.encode(store, "rw_good", "abc12345", 2, 3, LEAD)

    async def go(app, pilot):
        await _join(app, pilot, code)
        assert app.query_one("#pcs", Input).value == "3" and app.query_one("#pc_index", Input).value == "2"
        assert app.query_one("#max_prs", Input).value == "8000" and app.query_one("#window_to", Input).value == "2026-07-31"
        assert app.query_one("#only_domain", Input).value == "web-ts" and app.query_one("#llm", Checkbox).value is False
        assert app.query_one("#join_code", Input).value == ""                 # the secret is not left on screen
        assert "joined as pc2of3-abc12345" in _text(app, "store_line")
        assert app._lead_differences() == []                                  # the form now equals the lead's flags
    _run(go)
    env = (tmp_path / ".env").read_text()
    assert f"RIFFLE_SYNC_URL={store}" in env and "RIFFLE_SYNC_TOKEN=rw_good" in env
    assert oct((tmp_path / ".env").stat().st_mode & 0o777) == "0o600"


def test_a_bad_or_revoked_code_changes_nothing(store, tmp_path, monkeypatch):
    monkeypatch.setattr(tui, "HERE", str(tmp_path))

    async def go(app, pilot):
        before = app.query_one("#max_prs", Input).value
        await _join(app, pilot, join.encode(store, "rw_revoked", "abc12345", 1, 3, LEAD))
        assert app.query_one("#max_prs", Input).value == before and app.joined is None
        await _join(app, pilot, "riffle1.garbage")
        assert app.joined is None
    _run(go)
    assert not (tmp_path / ".env").exists()


def test_start_is_stopped_when_the_form_leaves_the_leads_settings(store, tmp_path, monkeypatch):
    monkeypatch.setattr(tui, "HERE", str(tmp_path))
    (tmp_path / "repos_web_ts.txt").write_text("https://github.com/o/a\n")
    code = join.encode(store, "rw_good", "abc12345", 2, 3, LEAD)

    async def go(app, pilot):
        await _join(app, pilot, code)
        app.query_one("#max_prs", Input).value = "100"                       # a volunteer "tidies" a setting
        app.query_one("#pc_index", Input).value = "1"                        # and picks the wrong PC number
        app.action_start()
        await pilot.pause()
        log = "\n".join(app.log_lines)
        assert app.proc is None and "NOT STARTED" in log
        assert "--max-prs: lead 8000, yours 100" in log and "--shard: lead '2/3', yours '1/3'" in log
        app.action_join()                                                    # F2 with the box empty restores them
        await pilot.pause()
        assert app._lead_differences() == [] and app.query_one("#max_prs", Input).value == "8000"
    _run(go)


def test_settings_that_are_about_this_machine_may_differ(store, tmp_path, monkeypatch):
    monkeypatch.setattr(tui, "HERE", str(tmp_path))

    async def go(app, pilot):
        await _join(app, pilot, join.encode(store, "rw_good", "abc12345", 2, 3, LEAD))
        app.query_one("#workers", Input).value = "2"
        app.query_one("#clone_dir", Input).value = "elsewhere"
        app.query_one("#keep_clones", Checkbox).value = True
        assert app._lead_differences() == []
    _run(go)


def test_stats_panel_shows_uploads():
    async def go(app, pilot):
        assert app._sync_text() is None
        app._apply({"ev": "sync", "t": 1000.0, "on": "pc1of2-abc"})
        assert "uploading as pc1of2-abc" in app._sync_text()
        app._apply({"ev": "sync", "t": __import__("time").time(), "why": "repo done", "sent": 3, "skipped": 4, "failed": 1, "stopped": "store unreachable"})
        t = app._sync_text()
        assert "3 files sent" in t and "1 failed" in t and "store unreachable" in t
        app._apply({"ev": "sync", "t": 1.0, "off": "token for split abc, flags make xyz"})
        assert "OFF for this run" in app._sync_text()
    _run(go)
