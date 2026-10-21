"""The lead's repo list and settings travel through the store: a volunteer needs no matching file, sees what the
lead chose, and a store cannot make the miner write outside its own folder."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import urllib.request

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
MINER = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", "pipelines", "ingestion", "repo_miner"))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "server"))
sys.path.insert(0, MINER)
os.environ.setdefault("RIFFLE_NO_DOCTOR", "1")
os.environ.setdefault("RIFFLE_NO_PLAN_USAGE", "1")
os.environ.setdefault("RIFFLE_DASHBOARD", "0")

import app as store_app  # noqa: E402
import join  # noqa: E402
import sync  # noqa: E402
import tokens  # noqa: E402

REPOS = "".join(f"https://github.com/pallets/{n}  # [~{c} commits] [domain: test]\n" for n, c in
                (("flask", 5500), ("click", 2700), ("jinja", 3000), ("itsdangerous", 900)))
FLAGS = ["--repos-file", "repos_test.txt", "--only-domain", "test", "--max-prs", "20", "--no-llm", "--no-szz", "--no-scanners"]


@pytest.fixture
def store(tmp_path):
    tp = os.path.join(str(tmp_path), "tokens.json")
    adm = tokens.issue(tp, "admins", "maria")
    s = app = store_app.serve(str(tmp_path), "127.0.0.1", 0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    s.url, s.adm, s.tp = f"http://127.0.0.1:{s.server_port}", adm, tp
    yield s
    s.shutdown()


def post(srv, path, body):
    req = urllib.request.Request(srv.url + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {srv.adm}", "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req))


def issue(srv, **extra):
    body = {"split_id": "706ba187", "n": 2, "days": 7, "flags": FLAGS, "miner_version": "6.10.3",
            "repos_name": "repos_test.txt", "repos_text": REPOS}
    body.update(extra)
    return post(srv, "/v1/admin/splits", body)["pcs"]


def test_a_pc_can_read_its_splits_settings_and_nothing_else(store):
    pcs = issue(store)
    meta = sync.fetch_split(store.url, pcs[1]["token"])
    assert meta["split_id"] == "706ba187" and meta["pc"] == "pc2of2-706ba187" and meta["n"] == 2
    assert meta["flags"] == FLAGS and meta["version"] == "6.10.3" and meta["issued_by"] == "maria"
    assert meta["repos_name"] == "repos_test.txt" and meta["repos_text"] == REPOS and meta["n_repos"] == 4
    assert meta["expires"] and meta["created"]
    assert sync.fetch_split(store.url, "rw_wrong") is None                       # a wrong token learns nothing
    rdr = tokens.issue(store.tp, "readers", "x")
    assert sync.fetch_split(store.url, rdr) is None and sync.fetch_split(store.url, store.adm) is None   # only a PC token
    assert sync.fetch_split("http://127.0.0.1:1", pcs[0]["token"]) is None      # an unreachable store is not an error
    unbound = tokens.issue(store.tp, "pcs", "loose")                             # a token that is not for a split
    assert sync.fetch_split(store.url, unbound) is None


def test_validation_and_the_admin_overview_never_leak_the_list(store):
    for bad in ({"repos_name": "../../etc/passwd"}, {"repos_name": "a/b"}, {"repos_text": "x" * 200_001}, {"repos_text": 5}):
        req = urllib.request.Request(store.url + "/v1/admin/splits", method="POST", headers={"Authorization": f"Bearer {store.adm}", "Content-Type": "application/json"},
                                     data=json.dumps({"split_id": "aa11bb22", "n": 1, "repos_name": "r.txt", "repos_text": "u", **bad}).encode())
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        assert e.value.code == 400
    issue(store)
    req = urllib.request.Request(store.url + "/v1/admin/overview", headers={"Authorization": f"Bearer {store.adm}"})
    d = json.load(urllib.request.urlopen(req))
    sp = d["splits"][0]
    assert sp["repos_name"] == "repos_test.txt" and sp["n_repos"] == 4 and "repos_text" not in sp
    assert "pallets/flask" not in json.dumps(d)


def test_rotating_keeps_the_list_and_who_issued_it(store, monkeypatch):
    monkeypatch.setenv("RIFFLE_PUBLIC_URL", "https://data.example.org")
    old = issue(store)
    req = urllib.request.Request(store.url + "/v1/admin/rotate", method="POST", data=json.dumps({"split_id": "706ba187"}).encode(),
                                 headers={"Authorization": f"Bearer {store.adm}", "Content-Type": "application/json", "X-Requested-With": "riffle"})
    new = json.load(urllib.request.urlopen(req))["pcs"]
    doc = join.decode(new[0]["code"])
    meta = sync.fetch_split(store.url, doc["t"])
    assert meta["repos_text"] == REPOS and meta["issued_by"] == "maria" and meta["flags"] == FLAGS      # nothing lost on rotation
    assert sync.fetch_split(store.url, old[0]["token"]) is None


def test_apply_split_writes_the_leads_list_and_points_the_flags_at_it(store, tmp_path):
    pcs = issue(store)
    doc = join.decode(join.encode(store.url, pcs[1]["token"], "706ba187", 2, 2, FLAGS))
    meta = sync.fetch_split(store.url, doc["t"])
    out = join.apply_split(doc, meta, str(tmp_path))
    rel = os.path.join("joined_repos", "706ba187", "repos_test.txt")
    assert out["a"][out["a"].index("--repos-file") + 1] == rel and (tmp_path / rel).read_text() == REPOS
    assert out["lead"]["issued_by"] == "maria" and out["lead"]["n_repos"] == 4 and out["lead"]["local_repos_file"] == rel
    assert doc["a"][1] == "repos_test.txt"                                       # the input is not changed
    # the flag given as --repos-file=X, or not given at all, is handled too
    eq = join.apply_split(dict(doc, a=["--repos-file=old.txt", "--max-prs", "20"]), meta, str(tmp_path))
    assert eq["a"][0] == f"--repos-file={rel}"
    none = join.apply_split(dict(doc, a=["--max-prs", "20"]), meta, str(tmp_path))
    assert none["a"][-2:] == ["--repos-file", rel]
    # the plan the volunteer sees is the one the lead's file makes
    info = join.describe(out, str(tmp_path))
    assert info["mine"] == ["pallets/click", "pallets/jinja"] and [r[2] for r in info["repos"]].count(1) == 2
    assert dict(info["settings"])["Max PRs per repo"] == 20 and dict(info["settings"])["LLM flags"] == "off"


def test_a_hostile_store_cannot_write_outside_the_miner_folder(tmp_path):
    doc = {"u": "http://x", "t": "rw_t", "s": "706ba187", "k": 1, "n": 2, "a": FLAGS}
    for name in ("../evil.txt", "a/b.txt", "/etc/passwd", ".hidden", "x" * 200, "", None, 5):
        out = join.apply_split(doc, {"repos_name": name, "repos_text": "u", "split_id": "706ba187"}, str(tmp_path))
        assert out["a"] == FLAGS, name                                          # unsafe name: the code's own flags stand
    out = join.apply_split(dict(doc, s="../up"), {"repos_name": "r.txt", "repos_text": "u"}, str(tmp_path))
    assert out["a"] == FLAGS                                                     # and an unsafe split id never makes a path
    assert not (tmp_path.parent / "evil.txt").exists() and list(tmp_path.iterdir()) == []
    assert join.apply_split(doc, None, str(tmp_path))["a"] == FLAGS             # an older store: nothing changes
    assert join.apply_split(doc, {"repos_name": "r.txt", "repos_text": "   "}, str(tmp_path))["a"] == FLAGS


def test_the_cli_join_fetches_the_leads_list(store, tmp_path, monkeypatch, capsys):
    pcs = issue(store)
    code = join.encode(store.url, pcs[1]["token"], "706ba187", 2, 2, FLAGS)
    monkeypatch.setattr(join, "HERE", str(tmp_path))
    assert join.main([code]) == 0
    out = capsys.readouterr().out
    assert "lead's repo list (repos_test.txt, 4 repos)" in out and "joined_repos/706ba187/repos_test.txt" in out
    assert "--repos-file joined_repos/706ba187/repos_test.txt" in out and "--shard 2/2" in out
    saved = join.load_state(str(tmp_path / ".join.json"))
    assert saved["lead"]["issued_by"] == "maria" and "joined_repos" in " ".join(saved["a"])


textual = pytest.importorskip("textual")
from textual.widgets import DataTable, Input, Static  # noqa: E402

import tui  # noqa: E402


def _run(coro):
    async def go():
        a = tui.RiffleTUI()
        async with a.run_test(size=(150, 50)) as pilot:
            await pilot.pause()
            return await coro(a, pilot)
    return asyncio.run(go())


def test_the_tui_shows_the_leads_settings_and_your_share(store, tmp_path, monkeypatch):
    pcs = issue(store)
    monkeypatch.setattr(tui, "HERE", str(tmp_path))
    for k in ("RIFFLE_SYNC_URL", "RIFFLE_SYNC_TOKEN"):
        monkeypatch.setenv(k, "")
    code = join.encode(store.url, pcs[1]["token"], "706ba187", 2, 2, FLAGS)

    async def go(a, pilot):
        card = a.query_one("#lead_card", Static)
        assert card.display is False                                           # nothing to show before joining
        a.query_one("#join_code", Input).value = code
        a.action_join()
        await pilot.pause()
        await a.workers.wait_for_complete()
        await pilot.pause()
        text = str(card.render())
        assert card.display is True and "repos_test.txt" in text and "4 repos" in text
        assert "yours: pallets/click, pallets/jinja" in text and "20 PRs/repo" in text and "split 706ba187" in text
        assert a.query_one("#repos_file", Input).value == os.path.join("joined_repos", "706ba187", "repos_test.txt")
        assert a.query_one("#repos_file", Input).disabled and a.query_one("#repos_file", Input).tooltip
        a.action_lead_settings()
        await pilot.pause()
        screen = a.screen
        assert isinstance(screen, tui.LeadSettingsScreen)
        repos = screen.query_one("#ls_repos", DataTable)
        rows = [[str(c) for c in repos.get_row_at(i)] for i in range(repos.row_count)]
        assert len(rows) == 4 and sum("you" in r[2] for r in rows) == 2
        assert {r[0] for r in rows if "you" in r[2]} == {"pallets/click", "pallets/jinja"}
        info = str(screen.query_one("#ls_info", Static).render())
        assert "maria" in info and "PC 2 of 2" in info and "6.10.3" in info
        a.pop_screen()
        await pilot.pause()
        a.action_leave()
        await pilot.pause()
        assert card.display is False
    _run(go)


def test_the_card_comes_back_after_a_restart(store, tmp_path, monkeypatch):
    pcs = issue(store)
    monkeypatch.setattr(tui, "HERE", str(tmp_path))
    for k in ("RIFFLE_SYNC_URL", "RIFFLE_SYNC_TOKEN"):
        monkeypatch.setenv(k, "")
    code = join.encode(store.url, pcs[0]["token"], "706ba187", 1, 2, FLAGS)

    async def first(a, pilot):
        a.query_one("#join_code", Input).value = code
        a.action_join()
        await pilot.pause()
        await a.workers.wait_for_complete()
    _run(first)

    async def second(a, pilot):
        await a.workers.wait_for_complete()
        await pilot.pause()
        assert "yours: pallets/flask, pallets/itsdangerous" in str(a.query_one("#lead_card", Static).render())
    _run(second)
