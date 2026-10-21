"""
Tokens per split and the join flow (ADR 0015): the lead's admin token mints one write
token per PC when the split is planned, each PC joins with one code, and a token can
write only under its own split.
"""
from __future__ import annotations

import io
import json
import os
import sys
import threading
import urllib.error
import urllib.request

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "server"))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", "pipelines", "ingestion", "repo_miner")))

import app  # noqa: E402
import tokens  # noqa: E402


@pytest.fixture
def store(tmp_path):
    tp = os.path.join(str(tmp_path), "tokens.json")
    admin, reader = tokens.issue(tp, "admins", "lead"), tokens.issue(tp, "readers", "lead")
    s = app.serve(str(tmp_path), "127.0.0.1", 0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    s.url, s.admin, s.reader, s.tp = f"http://127.0.0.1:{s.server_port}", admin, reader, tp
    yield s
    s.shutdown()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def call(srv, method, path, token=None, body=None):
    h = {"Authorization": f"Bearer {token}"} if token else {}
    data = json.dumps(body).encode() if isinstance(body, dict) else body
    if isinstance(body, dict):
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(srv.url + path, data=data, method=method, headers=h)
    try:
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def issue(srv, split="ab12cd34", n=3, days=None):
    code, body = call(srv, "POST", "/v1/admin/splits", srv.admin, {"split_id": split, "n": n, "days": days})
    assert code == 200, body
    return json.loads(body)["pcs"]


def test_only_an_admin_can_issue_and_nothing_else_does(store):
    body = {"split_id": "ab12cd34", "n": 2}
    assert call(store, "POST", "/v1/admin/splits", None, body)[0] == 401
    assert call(store, "POST", "/v1/admin/splits", store.reader, body)[0] == 401      # a reader cannot mint tokens
    pcs = issue(store)
    assert call(store, "POST", "/v1/admin/splits", pcs[0]["token"], body)[0] == 401   # nor can a PC
    assert call(store, "GET", "/api/runs", store.admin)[0] == 401                     # an admin reads nothing
    assert call(store, "PUT", "/v1/files/6.6.0/ab12cd34/logs/a.log", store.admin, b"x")[0] == 401


def test_issued_tokens_are_named_by_pc_and_split_and_stored_hashed(store):
    pcs = issue(store, "ab12cd34", 3)
    assert [p["pc"] for p in pcs] == ["pc1of3-ab12cd34", "pc2of3-ab12cd34", "pc3of3-ab12cd34"]
    assert all(p["token"].startswith("rw_") for p in pcs)
    raw = open(store.tp).read()
    assert not any(p["token"] in raw for p in pcs) and '"split": "ab12cd34"' in raw
    assert json.loads(call(store, "GET", "/v1/whoami", pcs[1]["token"])[1])["name"] == "pc2of3-ab12cd34"
    code, body = call(store, "POST", "/v1/admin/splits", store.admin, {"split_id": "BAD ID", "n": 2})
    assert code == 400


def test_a_token_writes_only_under_its_own_split(store):
    t = issue(store, "ab12cd34", 2)[0]["token"]
    assert call(store, "PUT", "/v1/files/6.6.0/ab12cd34/logs/a.log", t, b"x")[0] == 200
    assert call(store, "PUT", "/v1/files/6.7.0/ab12cd34/run.json", t, b"{}")[0] == 200     # any miner version is fine
    code, body = call(store, "PUT", "/v1/files/6.6.0/other000/logs/a.log", t, b"x")
    assert code == 403 and b"split ab12cd34" in body
    assert call(store, "PUT", "/v1/files/6.6.0/nosplit/logs/a.log", t, b"x")[0] == 403    # an unsharded run is not its split
    assert call(store, "PUT", "/v1/files/onlyone", t, b"x")[0] in (400, 403)


def test_reissuing_a_split_rotates_and_revoking_stops_it(store):
    old = issue(store, "ab12cd34", 2)
    new = issue(store, "ab12cd34", 2)
    assert call(store, "GET", "/v1/whoami", old[0]["token"])[0] == 401
    assert call(store, "GET", "/v1/whoami", new[0]["token"])[0] == 200
    other = issue(store, "ee55ff66", 1)
    code, body = call(store, "POST", "/v1/admin/revoke", store.admin, {"split_id": "ab12cd34"})
    assert sorted(json.loads(body)["revoked"]) == ["pc1of2-ab12cd34", "pc2of2-ab12cd34"]
    assert call(store, "GET", "/v1/whoami", new[1]["token"])[0] == 401
    assert call(store, "GET", "/v1/whoami", other[0]["token"])[0] == 200                  # another split is untouched
    assert json.loads(call(store, "POST", "/v1/admin/revoke", store.admin, {"name": "pc1of1-ee55ff66"})[1])["revoked"]
    assert call(store, "GET", "/v1/whoami", other[0]["token"])[0] == 401


def test_tokens_can_expire(store):
    t = issue(store, "ab12cd34", 1, days=0.000001)[0]["token"]
    import time
    time.sleep(0.2)
    assert call(store, "GET", "/v1/whoami", t)[0] == 401
    assert call(store, "PUT", "/v1/files/6.6.0/ab12cd34/logs/a.log", t, b"x")[0] == 401


def test_admin_lists_pcs_with_their_last_upload(store):
    pcs = issue(store, "ab12cd34", 2)
    call(store, "PUT", "/v1/files/6.6.0/ab12cd34/run.json", pcs[0]["token"], b'{"reason":"repo done"}')
    rows = json.loads(call(store, "GET", "/v1/admin/pcs", store.admin)[1])["pcs"]
    by = {r["pc"]: r for r in rows}
    assert by["pc1of2-ab12cd34"]["last_upload"] and by["pc2of2-ab12cd34"]["last_upload"] is None
    assert by["pc1of2-ab12cd34"]["split"] == "ab12cd34"
    assert call(store, "GET", "/v1/admin/pcs", store.reader)[0] == 401


# -- the miner's side ---------------------------------------------------------

def test_join_code_roundtrip_and_damage():
    import join
    code = join.encode("https://x.example", "rw_secret", "ab12cd34", 2, 3, ["--repos-file", "repos_web_ts.txt"])
    doc = join.decode(" " + code[:20] + "\n" + code[20:] + " ")           # pasted with a line break
    assert (doc["u"], doc["t"], doc["s"], doc["k"], doc["n"]) == ("https://x.example", "rw_secret", "ab12cd34", 2, 3)
    assert join.command(doc) == "python build_dataset.py --repos-file repos_web_ts.txt --shard 2/3"
    for bad in ("hello", "riffle1.", "riffle1.@@@@", join.PREFIX + "e30"):
        with pytest.raises(ValueError):
            join.decode(bad)


def test_write_env_keeps_other_lines_and_is_private(tmp_path):
    import join
    env = tmp_path / ".env"
    env.write_text("GITHUB_TOKEN=ghp_x\nRIFFLE_SYNC_URL=old\n# note\nRIFFLE_SYNC_URL=dup\n")
    join.write_env(str(env), {"RIFFLE_SYNC_URL": "https://new", "RIFFLE_SYNC_TOKEN": "rw_t"})
    lines = env.read_text().splitlines()
    assert lines == ["GITHUB_TOKEN=ghp_x", "RIFFLE_SYNC_URL=https://new", "# note", "RIFFLE_SYNC_TOKEN=rw_t"]
    assert oct(env.stat().st_mode & 0o777) == "0o600"


def test_plan_flags_are_stripped_from_the_command_pcs_run():
    import build_dataset as bd
    argv = ["--repos-file", "r.txt", "--only-domain", "web-ts", "--shard-plan", "3", "--issue-tokens", "--token-days=30",
            "--tokens-out", "codes.txt", "--window-from", "2023-07-01", "--max-prs", "8000"]
    assert bd._run_flags(argv) == ["--repos-file", "r.txt", "--only-domain", "web-ts", "--window-from", "2023-07-01", "--max-prs", "8000"]


def test_plan_issues_codes_and_a_pc_joins_with_one(store, tmp_path, monkeypatch, capsys):
    import types
    import build_dataset as bd
    import join
    monkeypatch.setenv("RIFFLE_SYNC_URL", store.url)
    monkeypatch.setenv("RIFFLE_ADMIN_TOKEN", store.admin)
    monkeypatch.setattr(sys, "argv", ["build_dataset.py", "--repos-file", "r.txt", "--shard-plan", "2", "--issue-tokens"])
    out_file = tmp_path / "codes.txt"
    bd._issue_tokens(types.SimpleNamespace(token_days=30, tokens_out=str(out_file)), "ab12cd34", 2)
    printed = capsys.readouterr().out
    codes = [ln.split("python join.py ", 1)[1] for ln in printed.splitlines() if "python join.py riffle1." in ln]
    assert len(codes) == 2 and oct(out_file.stat().st_mode & 0o777) == "0o600"

    monkeypatch.setattr(join, "HERE", str(tmp_path))
    monkeypatch.delenv("RIFFLE_SYNC_URL"); monkeypatch.delenv("RIFFLE_ADMIN_TOKEN")
    assert join.main([codes[1]]) == 0
    said = capsys.readouterr().out
    assert "PC 2 of 2" in said and "python build_dataset.py --repos-file r.txt --shard 2/2" in said
    env = (tmp_path / ".env").read_text()
    assert f"RIFFLE_SYNC_URL={store.url}" in env and "RIFFLE_SYNC_TOKEN=rw_" in env

    import sync
    tok = env.split("RIFFLE_SYNC_TOKEN=")[1].split()[0]
    me = sync.whoami(store.url, tok)
    assert me["ok"] and me["name"] == "pc2of2-ab12cd34" and me["split"] == "ab12cd34"

    # revoked: the join check says so, and nothing is saved
    call(store, "POST", "/v1/admin/revoke", store.admin, {"split_id": "ab12cd34"})
    (tmp_path / ".env").unlink()
    assert join.main([codes[0]]) == 1 and not (tmp_path / ".env").exists()
    assert "not accept" in capsys.readouterr().err


def test_whoami_explains_each_failure(store):
    import sync
    assert sync.whoami(store.url, store.reader)["why"].startswith("this is a reader token")
    assert "not accept" in sync.whoami(store.url, "rw_nope")["why"]
    assert sync.whoami("http://127.0.0.1:1", "rw_x")["unreachable"]
    assert sync.whoami("", "")["unset"]


def test_a_run_turns_sync_off_when_the_token_is_for_another_split(store, monkeypatch, capsys):
    import types
    import build_dataset as bd
    t = issue(store, "ab12cd34", 1)[0]["token"]
    monkeypatch.setenv("RIFFLE_SYNC_URL", store.url)
    monkeypatch.setenv("RIFFLE_SYNC_TOKEN", t)
    for sid, stays in (("ab12cd34", True), ("zz99zz99", False), (None, False)):
        monkeypatch.setattr(bd, "_SYNC", {"args": types.SimpleNamespace(split_id=sid, out="x.parquet"), "counts": {}})
        bd._sync_begin([("o", "a", "")], {})
        assert (bd._SYNC is not None) is stays
    assert "token is for split ab12cd34" in capsys.readouterr().out
    monkeypatch.setenv("RIFFLE_SYNC_TOKEN", "rw_revoked")
    monkeypatch.setattr(bd, "_SYNC", {"args": types.SimpleNamespace(split_id="ab12cd34", out="x"), "counts": {}})
    bd._sync_begin([], {})
    assert bd._SYNC is None and "OFF for this run" in capsys.readouterr().out
