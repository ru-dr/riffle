"""The lead's TUI screen against a real store: save the admin token, issue one join code per PC, copy them,
see the PC tokens, and revoke one PC or a whole split (two presses)."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
MINER = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", "pipelines", "ingestion", "repo_miner"))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "server"))
sys.path.insert(0, MINER)
os.environ.setdefault("RIFFLE_NO_DOCTOR", "1")
os.environ.setdefault("RIFFLE_NO_PLAN_USAGE", "1")
os.environ.setdefault("RIFFLE_DASHBOARD", "0")

pytest.importorskip("textual")
from textual.widgets import DataTable, Input, Static  # noqa: E402

import app as store_app  # noqa: E402
import join  # noqa: E402
import sync  # noqa: E402
import tokens  # noqa: E402
import tui  # noqa: E402


@pytest.fixture
def lead(tmp_path, monkeypatch):
    tp = os.path.join(str(tmp_path), "tokens.json")
    admin = tokens.issue(tp, "admins", "lead")
    reader = tokens.issue(tp, "readers", "lead")
    srv = store_app.serve(str(tmp_path), "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    work = tmp_path / "miner"
    work.mkdir()
    (work / "repos.txt").write_text("".join(f"https://github.com/o/r{i}  # [~{1000 * i} commits] [domain: web-ts]\n" for i in range(1, 7)))
    monkeypatch.setattr(tui, "HERE", str(work))
    monkeypatch.setenv("RIFFLE_SYNC_URL", f"http://127.0.0.1:{srv.server_port}")
    monkeypatch.setenv("RIFFLE_ADMIN_TOKEN", "")
    yield type("L", (), {"url": f"http://127.0.0.1:{srv.server_port}", "admin": admin, "reader": reader, "work": work, "tp": tp})
    srv.shutdown()


def _run(coro):
    async def go():
        app = tui.RiffleTUI()
        async with app.run_test(size=(180, 70)) as pilot:
            await pilot.pause()
            return await coro(app, pilot)
    return asyncio.run(go())


async def _settle(app, pilot):
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


def _text(screen, wid):
    return str(screen.query_one(f"#{wid}", Static).render())


def test_save_the_admin_token_only_if_it_really_is_one(lead):
    async def go(app, pilot):
        app.action_lead(); await pilot.pause()
        s = app.screen
        assert "not saved" in _text(s, "lead_status")
        s.query_one("#adm_token", Input).value = "rd_wrong"; s._save_token(); await _settle(app, pilot)
        assert "starts with ad_" in _text(s, "lead_msg")
        s.query_one("#adm_token", Input).value = lead.reader; s._save_token(); await _settle(app, pilot)
        s.query_one("#adm_token", Input).value = "ad_not-a-real-one"; s._save_token(); await _settle(app, pilot)
        assert "Not saved" in _text(s, "lead_msg") and os.environ["RIFFLE_ADMIN_TOKEN"] == ""
        s.query_one("#adm_token", Input).value = lead.admin; s._save_token(); await _settle(app, pilot)
        assert "saved to .env" in _text(s, "lead_msg") and os.environ["RIFFLE_ADMIN_TOKEN"] == lead.admin
        assert s.query_one("#adm_token", Input).value == ""                  # not left on screen
    _run(go)
    env = (lead.work / ".env").read_text()
    assert f"RIFFLE_ADMIN_TOKEN={lead.admin}" in env and oct((lead.work / ".env").stat().st_mode & 0o777) == "0o600"


def test_issue_codes_copy_them_then_revoke(lead, monkeypatch):
    monkeypatch.setenv("RIFFLE_ADMIN_TOKEN", lead.admin)
    copied = []

    async def go(app, pilot):
        monkeypatch.setattr(app, "_copy", lambda text: copied.append(text) or True)
        app.query_one("#pcs", Input).value = "3"
        app.query_one("#pc_index", Input).value = "1"
        app.query_one("#workers", Input).value = "9"                          # a machine setting: must not travel
        app.action_lead(); await _settle(app, pilot)
        s = app.screen
        s.query_one("#adm_days", Input).value = "7"
        s._issue(); await _settle(app, pilot)
        assert len(s.codes) == 3 and s.split_id and "3 codes" in _text(s, "lead_msg")
        assert s.query_one("#codes", DataTable).row_count == 3
        docs = [join.decode(c) for _l, c in s.codes]
        assert [d["k"] for d in docs] == [1, 2, 3] and all(d["n"] == 3 and d["s"] == s.split_id for d in docs)
        assert all(d["u"] == lead.url for d in docs)
        argv = docs[0]["a"]
        assert "--only-domain" not in argv or argv[argv.index("--only-domain") + 1] == ""        # form default
        assert "--workers" not in argv and "--clone-dir" not in argv and "--shard" not in argv      # nothing machine-specific
        assert "--repos-file" in argv
        for d in docs:                                                          # each code is a working, split-bound token
            me = sync.whoami(d["u"], d["t"])
            assert me["ok"] and me["split"] == s.split_id and me["expires"]
        s.query_one("#codes", DataTable).move_cursor(row=1)
        s._copy_one(); s._copy_all()
        assert copied[0] == s.codes[1][1] and copied[1].count("\n") == 2
        s._save_codes()
        saved = lead.work / f"join_codes_{s.split_id}.txt"
        assert saved.exists() and oct(saved.stat().st_mode & 0o777) == "0o600"

        # the PC table lists them, and revoke asks twice
        await _settle(app, pilot)
        t = s.query_one("#pcs_tbl", DataTable)
        assert t.row_count == 3
        t.move_cursor(row=0)
        first = s._pcs[0]["pc"]
        s._revoke_pc(); await _settle(app, pilot)
        assert "again to revoke" in _text(s, "lead_msg") and sync.whoami(lead.url, docs[0]["t"])["ok"]   # first press changes nothing
        s._revoke_pc(); await _settle(app, pilot)
        assert first in _text(s, "lead_msg") and not sync.whoami(lead.url, docs[0]["t"])["ok"]
        assert sync.whoami(lead.url, docs[1]["t"])["ok"] and s.query_one("#pcs_tbl", DataTable).row_count == 2
        s.query_one("#pcs_tbl", DataTable).move_cursor(row=0)
        s._revoke_split(); s._revoke_split(); await _settle(app, pilot)
        assert not sync.whoami(lead.url, docs[1]["t"])["ok"] and not sync.whoami(lead.url, docs[2]["t"])["ok"]
        assert s.query_one("#pcs_tbl", DataTable).row_count == 0
    _run(go)


def test_it_explains_what_is_missing(lead):
    async def go(app, pilot):
        app.query_one("#pcs", Input).value = "1"
        app.action_lead(); await pilot.pause()
        s = app.screen
        s._issue()
        assert "admin token first" in _text(s, "lead_msg")
        os.environ["RIFFLE_ADMIN_TOKEN"] = lead.admin
        s._issue()
        assert "2 or more" in _text(s, "lead_msg")                             # one PC needs no split
        app.query_one("#pcs", Input).value = "2"
        app.query_one("#repos_file", Input).value = "missing.txt"
        s._issue(); await _settle(app, pilot)
        assert "Could not issue codes" in _text(s, "lead_msg") and s.codes == []
    _run(go)


def test_the_lead_can_join_its_own_pc_from_lead_tools(lead, monkeypatch):
    """After issuing codes the lead's PC joins as the selected row without pasting the code anywhere."""
    monkeypatch.setenv("RIFFLE_ADMIN_TOKEN", lead.admin)

    async def go(app, pilot):
        app.query_one("#pcs", Input).value = "2"
        app.query_one("#pc_index", Input).value = "1"
        app.action_lead(); await _settle(app, pilot)
        s = app.screen
        s._join_here()                                                          # nothing issued yet: it says so
        assert "Issue codes first" in _text(s, "lead_msg")
        s.query_one("#adm_days", Input).value = "7"
        s._issue(); await _settle(app, pilot)
        s.query_one("#codes", DataTable).move_cursor(row=0)
        s._join_here(); await _settle(app, pilot)
        await pilot.pause(); await app.workers.wait_for_complete(); await pilot.pause()
        assert app.joined and app.joined["k"] == 1 and app.joined["n"] == 2
        assert "joined as pc1of2" in str(app.query_one("#store_line", Static).render())
        assert os.environ.get("RIFFLE_SYNC_TOKEN", "").startswith("rw_")
        assert app.query_one("#join_code", Input).value == ""                   # the code does not stay on screen
    _run(go)
