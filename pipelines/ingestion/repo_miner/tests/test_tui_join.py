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


def test_f4_opens_the_store_and_copies_the_saved_read_token(monkeypatch):
    opened, copied = [], []
    monkeypatch.setattr(tui.webbrowser, "open", lambda url: opened.append(url))

    async def go(app, pilot):
        monkeypatch.setattr(app, "_copy", lambda text: copied.append(text) or True)
        monkeypatch.setenv("RIFFLE_SYNC_URL", "")
        app.action_open_store()                                            # not joined: nothing to open, and it says so
        assert opened == [] and copied == []
        monkeypatch.setenv("RIFFLE_SYNC_URL", "https://s.example/")
        monkeypatch.setenv("RIFFLE_READ_TOKEN", "")
        app.action_open_store()
        assert opened == ["https://s.example"] and copied == []            # no read token saved: opens the page only
        monkeypatch.setenv("RIFFLE_READ_TOKEN", "rd_secret")
        app.action_open_store()
        assert opened[-1] == "https://s.example" and copied == ["rd_secret"]
    _run(go)


# -- the lead's settings are locked after joining, and remembered ------------------------------------

MACHINE_IDS = ("clone_dir", "workers", "llm_conc", "remine", "quota_wait", "skip_existing", "keep_clones", "auto_remine", "skip_big", "big_n")


def _locked(app, wid):
    return app.query_one(f"#{wid}").disabled


def test_joining_locks_the_leads_settings_but_not_the_machine_ones(store, tmp_path, monkeypatch):
    monkeypatch.setattr(tui, "HERE", str(tmp_path))
    code = join.encode(store, "rw_good", "abc12345", 2, 3, LEAD)

    async def go(app, pilot):
        assert not any(_locked(app, w) for w in tui.LOCKED_IDS)            # before joining everything is editable
        assert app.query_one("#leave").display is False
        await _join(app, pilot, code)
        assert all(_locked(app, w) for w in tui.LOCKED_IDS), [w for w in tui.LOCKED_IDS if not _locked(app, w)]
        assert not any(_locked(app, w) for w in MACHINE_IDS)               # workers, clone dir, resume switches stay yours
        assert app.query_one("#leave").display is True
        assert "locked" in _text(app, "join_msg")
        # a run starting and ending must not unlock them
        app._set_running(True)
        assert all(_locked(app, w) for w in tui.LOCKED_IDS) and all(_locked(app, w) for w in MACHINE_IDS)
        app._set_running(False)
        assert all(_locked(app, w) for w in tui.LOCKED_IDS) and not any(_locked(app, w) for w in MACHINE_IDS)
    _run(go)


def test_the_locked_settings_come_back_after_a_restart(store, tmp_path, monkeypatch):
    monkeypatch.setattr(tui, "HERE", str(tmp_path))
    code = join.encode(store, "rw_good", "abc12345", 2, 3, LEAD)

    async def first(app, pilot):
        await _join(app, pilot, code)
    _run(first)
    assert (tmp_path / ".join.json").exists() and oct((tmp_path / ".join.json").stat().st_mode & 0o777) == "0o600"

    async def second(app, pilot):                                          # a new TUI process: nothing is typed in
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.joined and app.lead_argv[-2:] == ["--shard", "2/3"]
        assert app.query_one("#pcs", Input).value == "3" and app.query_one("#pc_index", Input).value == "2"
        assert app.query_one("#max_prs", Input).value == "8000" and app.query_one("#only_domain", Input).value == "web-ts"
        assert all(_locked(app, w) for w in tui.LOCKED_IDS) and app.query_one("#leave").display is True
    _run(second)


def test_a_remembered_join_is_ignored_when_the_store_no_longer_accepts_it(store, tmp_path, monkeypatch):
    monkeypatch.setattr(tui, "HERE", str(tmp_path))
    join.save_state(str(tmp_path / ".join.json"), {"u": store, "t": "rw_x", "s": "other999", "k": 1, "n": 2, "a": LEAD})
    monkeypatch.setenv("RIFFLE_SYNC_URL", store)
    monkeypatch.setenv("RIFFLE_SYNC_TOKEN", "rw_good")                      # the token's split is abc12345, the file says other999

    async def go(app, pilot):
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.joined is None and not any(_locked(app, w) for w in tui.LOCKED_IDS)
    _run(go)
    (tmp_path / ".join.json").write_text("{not json")                       # a damaged file is ignored, never an error
    _run(go)


def test_leaving_the_split_unlocks_the_form_and_forgets_the_settings(store, tmp_path, monkeypatch):
    monkeypatch.setattr(tui, "HERE", str(tmp_path))
    code = join.encode(store, "rw_good", "abc12345", 2, 3, LEAD)

    async def go(app, pilot):
        await _join(app, pilot, code)
        assert (tmp_path / ".join.json").exists()
        app.action_leave()
        await pilot.pause()
        assert not any(_locked(app, w) for w in tui.LOCKED_IDS) and app.query_one("#leave").display is False
        assert app.joined is None and app.lead_argv is None and not (tmp_path / ".join.json").exists()
        assert app._lead_differences() == []                                # with no lead, nothing to differ from
        assert "Unlocked" in _text(app, "join_msg")
    _run(go)


def test_the_join_state_helpers_round_trip(tmp_path):
    p = str(tmp_path / ".join.json")
    assert join.load_state(p) is None
    join.save_state(p, {"u": "https://s", "t": "rw_t", "s": "abc", "k": 1, "n": 2, "a": ["--max-prs", "20"]})
    assert join.load_state(p)["a"] == ["--max-prs", "20"]
    join.clear_state(p)
    join.clear_state(p)                                                     # clearing twice is fine
    assert join.load_state(p) is None


def test_text_from_the_store_is_shown_as_text_not_as_markup(tmp_path, monkeypatch):
    """A name, file name or reason that comes from a join code or the store may contain [brackets]; Textual markup such
    as [@click=app.quit] would make the screen act on it."""
    monkeypatch.setattr(tui, "HERE", str(tmp_path))
    hostile = "[@click=app.quit]pwn[/] [b]bold[/b]"

    async def go(app, pilot):
        app._show_store({"ok": True, "name": hostile, "split": "[red]x"})
        line = str(app.query_one("#store_line", Static).render())
        assert "[@click=app.quit]" in line and "[b]bold[/b]" in line, line      # shown literally
        app._show_store({"ok": False, "why": "[@click=app.quit]no[/]"})
        assert "[@click=app.quit]" in str(app.query_one("#store_line", Static).render())
        app.joined = {"s": "706ba187", "k": 1, "n": 2, "a": LEAD,
                      "lead": {"repos_name": "[@click=app.quit]r.txt", "issued_by": "[b]x"}}
        app._show_card()
        assert "[@click=app.quit]r.txt" in str(app.query_one("#lead_card", Static).render())
        app._join_msg("Not saved: [@click=app.quit]x[/]", "bad")
        assert "[@click=app.quit]x" in str(app.query_one("#join_msg", Static).render())
        app.sync_info = {"t": 0, "off": "[@click=app.quit]token[/]"}
        assert "[@click=app.quit]" in app._sync_text().replace("\\[", "[")
    _run(go)


def test_a_store_that_is_down_at_start_does_not_unlock_the_lead_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(tui, "HERE", str(tmp_path))
    join.save_state(str(tmp_path / ".join.json"), {"u": "https://s.example", "t": "rw_good", "s": "abc12345", "k": 2, "n": 3, "a": LEAD})

    async def go(app, pilot):
        app._show_store({"ok": False, "unreachable": True, "why": "cannot reach the store"})
        assert app.joined and app.joined["s"] == "abc12345" and app.query_one("#max_prs", Input).disabled
        app.joined, app.lead_argv = None, None
        app._show_store({"ok": False, "why": "the store did not accept this token"})      # a real rejection: stays unlocked
        assert not app.joined
    _run(go)
