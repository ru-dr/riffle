"""Findings from an audit of the token, sync and refresh code. Each test states the behaviour that should hold; a failure
here is a bug that was found by reading the code and then reproduced."""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
MINER = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", "pipelines", "ingestion", "repo_miner"))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "server"))
sys.path.insert(0, MINER)

import app as store_app  # noqa: E402
import join  # noqa: E402
import sync  # noqa: E402
import tokens  # noqa: E402


@pytest.fixture
def store(tmp_path):
    tp = os.path.join(str(tmp_path), "tokens.json")
    adm = tokens.issue(tp, "admins", "lead")
    s = store_app.serve(str(tmp_path), "127.0.0.1", 0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    s.url, s.adm, s.tp, s.dir, s.port = f"http://127.0.0.1:{s.server_port}", adm, tp, str(tmp_path), s.server_port
    yield s
    s.shutdown()


def req(srv, method, path, token=None, body=None, headers=None):
    h = dict(headers or {})
    if token:
        h["Authorization"] = f"Bearer {token}"
    data = body if isinstance(body, bytes) else (json.dumps(body).encode() if body is not None else None)
    if isinstance(body, dict):
        h["Content-Type"] = "application/json"
    r = urllib.request.Request(srv.url + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


# -- tokens ---------------------------------------------------------------------------------------

def test_revoking_a_pc_by_name_cannot_delete_an_admin_or_reader_of_the_same_name(store):
    tokens.issue(store.tp, "readers", "pc1of2-abc")
    adm2 = tokens.issue(store.tp, "admins", "pc1of2-abc")                      # same name as a PC token
    pcs = json.loads(req(store, "POST", "/v1/admin/splits", store.adm, {"split_id": "abc", "n": 2})[1])["pcs"]
    req(store, "POST", "/v1/admin/revoke", store.adm, {"name": "pc1of2-abc"})  # what admin.py and the TUI send: a name, no kind
    assert tokens.who(store.tp, adm2) == ("admins", "pc1of2-abc")
    assert tokens.load(store.tp)["readers"].get("pc1of2-abc")
    assert tokens.who(store.tp, pcs[0]["token"]) is None                         # the PC token is what goes


def test_the_last_admin_cannot_revoke_itself_through_the_api(store):
    code, body = req(store, "POST", "/v1/admin/revoke", store.adm, {"name": "lead", "kind": "admins"})
    assert code == 409, body
    assert tokens.who(store.tp, store.adm) == ("admins", "lead")


def test_concurrent_token_changes_do_not_lose_updates(store):
    names = [f"pc{i}" for i in range(40)]
    threads = [threading.Thread(target=tokens.issue, args=(store.tp, "pcs", n)) for n in names]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(tokens.load(store.tp)["pcs"]) == sorted(names)


def test_a_token_cannot_be_born_expired_or_live_for_ever_by_mistake(store):
    for days in (-1, -0.5, 99999, "x"):
        code, _ = req(store, "POST", "/v1/admin/splits", store.adm, {"split_id": "aa11bb22", "n": 1, "days": days})
        assert code == 400, days


# -- the server's limits ----------------------------------------------------------------------------

def _raw(srv, text: bytes, wait=3.0):
    s = socket.create_connection(("127.0.0.1", srv.port), timeout=wait)
    s.sendall(text)
    try:
        return s.recv(4096)
    except socket.timeout:
        return b"TIMEOUT"
    finally:
        s.close()


def test_a_negative_content_length_is_refused_not_waited_on(store):
    pc = tokens.issue(store.tp, "pcs", "p1")
    out = _raw(store, f"PUT /v1/files/6.0.0/abc/logs/a.log HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {pc}\r\nContent-Length: -5\r\n\r\n".encode())
    assert out.startswith(b"HTTP/1.") and b" 4" in out.split(b"\r\n")[0], out[:80]
    out = _raw(store, b"POST /login HTTP/1.1\r\nHost: x\r\nContent-Length: -5\r\n\r\n")
    assert out != b"TIMEOUT", "a negative length on POST hangs the connection"


def test_the_handler_has_a_socket_timeout():
    assert store_app.make_handler.__code__ is not None
    import inspect
    assert "timeout" in inspect.getsource(store_app.make_handler), "no per-connection timeout: idle clients hold threads for ever"


def test_the_admin_post_body_limit_fits_a_real_repo_list(store):
    text = "".join(f"https://github.com/owner{i}/repo{i}  # [~{i} commits] [domain: web-ts]\n" for i in range(900))   # ~65 KB
    assert 40_000 < len(text) < 200_000
    code, body = req(store, "POST", "/v1/admin/splits", store.adm, {"split_id": "aa11bb22", "n": 2, "repos_name": "r.txt", "repos_text": text})
    assert code == 200, body


def test_a_pcs_run_json_cannot_break_the_admin_or_overview_pages(store):
    pcs = json.loads(req(store, "POST", "/v1/admin/splits", store.adm, {"split_id": "abc", "n": 1})[1])["pcs"]
    for bad in (b"[]", b'"x"', b"5", b"null"):
        code, _ = req(store, "PUT", "/v1/files/6.0.0/abc/run.json", pcs[0]["token"], bad)
        assert code == 400, bad
    code, _ = req(store, "GET", "/v1/admin/overview", store.adm)
    assert code == 200


def test_the_client_address_behind_a_proxy_is_the_one_the_proxy_added(store, monkeypatch):
    monkeypatch.setattr(store_app, "TRUST_PROXY", True)
    for i in range(25):                                    # one real client, a new fake address in front each time
        req(store, "GET", "/api/runs", f"wrong{i}", headers={"X-Forwarded-For": f"10.9.9.{i}, 203.0.113.7"})
    code, _ = req(store, "GET", "/api/runs", "wrong2", headers={"X-Forwarded-For": "10.1.1.1, 203.0.113.7"})
    assert code == 429, "spoofed leading addresses let one client dodge the throttle"


def test_search_refuses_a_catastrophic_pattern_quickly(store):
    pc = tokens.issue(store.tp, "pcs", "p2")
    rdr = tokens.issue(store.tp, "readers", "r")
    req(store, "PUT", "/v1/files/6.0.0/abc/logs/a.log", pc, ("a" * 40 + "!\n").encode())
    t = time.time()
    code, body = req(store, "GET", "/api/search?q=%28a%2B%29%2B%24&regex=1", rdr)
    assert time.time() - t < 3, "a nested-quantifier regex pinned the server"
    assert code in (200, 400)


def test_gcs_downloads_of_the_same_file_do_not_share_a_temp_name(tmp_path):
    import store as store_mod

    class Blob:
        generation, name = 1, "p/6/s/data/a.parquet"
        names = []

        def download_to_filename(self, path):
            Blob.names.append(path)
            open(path, "w").write("x")

    class Bucket:
        def get_blob(self, key):
            return Blob()

    st = store_mod.Store(str(tmp_path))
    st._bucket = Bucket()
    barrier = threading.Barrier(2)
    orig = os.replace

    def slow_replace(a, b):
        try:
            barrier.wait(timeout=2)
        except threading.BrokenBarrierError:
            pass
        return orig(a, b)
    os.replace = slow_replace
    try:
        ts = [threading.Thread(target=st.fetch, args=("p/6/s/data/a.parquet",)) for _ in range(2)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
    finally:
        os.replace = orig
    assert len(set(Blob.names)) == 2, Blob.names


# -- the miner's side --------------------------------------------------------------------------------

def test_a_join_code_cannot_carry_extra_lines_into_dot_env():
    evil = join.encode("http://x.example\nGITHUB_TOKEN=evil", "rw_t", "abc", 1, 2, [])
    with pytest.raises(ValueError):
        join.decode(evil)
    evil2 = join.encode("http://x.example", "rw_t\nLLM_BASE_URL=http://evil", "abc", 1, 2, [])
    with pytest.raises(ValueError):
        join.decode(evil2)
    evil3 = join.encode("http://x.example", "rw_t", "abc", 1, 2, ["--max-prs", "20\n--x"])
    with pytest.raises(ValueError):
        join.decode(evil3)


def test_plan_only_flags_are_removed_even_when_abbreviated():
    import build_dataset as bd
    assert bd.build_parser().allow_abbrev is False, "argparse accepts --shard-p, which _run_flags would not strip"


def test_only_this_runs_health_files_are_uploaded(tmp_path):
    (tmp_path / "health").mkdir()
    for n in ("o_a.json", "o_old.json"):
        (tmp_path / "health" / n).write_text('{"repo": "x"}')
    out = tmp_path / "d.parquet"
    out.write_text("x")
    files = sync.collect(str(out), str(tmp_path / "health"), logs=[], repos={"o/a"})
    sent = sorted(os.path.basename(p) for p, _k in files if "/health/" in _k or _k.startswith("health/"))
    assert sent == ["o_a.json"], sent


def test_a_hostile_store_cannot_make_the_miner_read_without_limit(monkeypatch):
    import io

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    big = b'{"split_id": "abc", "repos_text": "' + b"x" * 5_000_000 + b'"}'
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: Resp(big))
    assert sync.fetch_split("http://x", "rw_t") is None, "a 5 MB answer to a 200 KB question must be refused"


# -- second round: the items the first audit left open ----------------------------------------------------

def test_rotating_through_localhost_without_a_public_url_is_refused_and_changes_nothing(store, monkeypatch):
    monkeypatch.delenv("RIFFLE_PUBLIC_URL", raising=False)
    pcs = json.loads(req(store, "POST", "/v1/admin/splits", store.adm, {"split_id": "abc", "n": 2})[1])["pcs"]
    code, body = req(store, "POST", "/v1/admin/rotate", store.adm, {"split_id": "abc"})
    assert code == 409, body
    assert tokens.who(store.tp, pcs[0]["token"]) is not None                  # the old tokens still work


def test_a_failed_backup_is_retried_recorded_and_cleared_by_the_next_good_one(tmp_path):
    import backup
    calls = []

    class Blob:
        def upload_from_filename(self, path):
            calls.append(path)
            if len(calls) < 3:
                raise OSError("network down")

    class Bucket:
        def blob(self, key):
            return Blob()

    class Store:
        _bucket = Bucket()
    tp = tmp_path / "tokens.json"
    tp.write_text("{}")
    assert backup.push(Store, str(tp), tries=3, wait=0) is True and len(calls) == 3      # two failures, then it lands
    calls.clear()
    assert backup.push(Store, str(tp), tries=1, wait=0) is False
    assert backup.STATE["ok"] is False and "network down" in backup.STATE["error"]
    calls.extend([1, 2])
    assert backup.push(Store, str(tp), tries=1, wait=0) is True and backup.STATE["ok"] is True


def test_a_machine_rejoined_under_another_token_sends_everything_again(store, tmp_path):
    a = tokens.issue_split(store.tp, "abc", 2)
    run = tmp_path / "run"
    (run / "logs").mkdir(parents=True)
    (run / "logs" / "r.log").write_text("hello\n")
    out = tmp_path / "d.parquet"
    out.write_text("x")
    kw = dict(health_dir=str(run / "health"), logs=[str(run / "logs" / "r.log")], state_path=str(tmp_path / "state.json"))
    for k, v in (("RIFFLE_SYNC_URL", store.url), ("RIFFLE_SYNC_TOKEN", a[0]["token"])):
        os.environ[k] = v
    try:
        r1 = sync.sync(str(out), "6.0.0/abc", {"state": "x"}, **kw)
        assert r1["sent"] == 2 and not r1["failed"], r1
        assert sync.sync(str(out), "6.0.0/abc", {"state": "x"}, **kw)["sent"] == 0
        os.environ["RIFFLE_SYNC_TOKEN"] = a[1]["token"]                         # now this machine is PC 2
        assert sync.sync(str(out), "6.0.0/abc", {"state": "x"}, **kw)["sent"] == 2
    finally:
        os.environ.pop("RIFFLE_SYNC_URL", None)
        os.environ.pop("RIFFLE_SYNC_TOKEN", None)


def test_the_whole_sync_has_one_deadline_even_inside_a_file(tmp_path):
    f = tmp_path / "big"
    f.write_bytes(b"x" * 100)
    with open(f, "rb") as fh:
        p = sync._Prefix(fh, 100, deadline=time.monotonic() - 1)
        with pytest.raises(sync._OutOfTime):
            p.read(10)
    with open(f, "rb") as fh:
        assert sync._Prefix(fh, 100, deadline=time.monotonic() + 60).read(10) == b"x" * 10


def test_pages_name_their_assets_by_content_so_a_deploy_is_not_hidden_by_the_browser_cache(store):
    import ui
    assert len(ui.ASSET_V) == 10
    html = ui.shell("search", "Search")
    for f in ("riffle.css", "controls.js", "app.js"):
        assert f"/static/{f}?v={ui.ASSET_V}" in html, f
    code, body = req(store, "GET", f"/static/riffle.css?v={ui.ASSET_V}")
    assert code == 200 and b".selbtn" in body


def test_overview_and_admin_have_a_big_picture_and_the_reader_pages_poll_only_while_mining():
    js = open(os.path.join(os.path.dirname(store_app.__file__), "static", "app.js"), encoding="utf-8").read()
    assert "setInterval(draw" not in js and "setInterval(load" not in js, "a fixed timer polls even when nothing is mining"
    assert 'liveLoop(draw, { big: true, kind: "overview" })' in js and 'liveLoop(draw, { big: true, kind: "admin" })' in js and "liveLoop(load)" in js
    assert "bigpic" not in js and "bvOverview" in js and "bvAdmin" in js


def test_the_whole_log_can_be_opened_and_exported_with_or_without_a_filter(store):
    pc = tokens.issue(store.tp, "pcs", "pcx")
    rdr = tokens.issue(store.tp, "readers", "r")
    lines = [f"2026-10-21 10:00:{i % 60:02d} INFO [mine] pallets/flask pr={i}" if i % 10 else f"2026-10-21 10:00:00 ERROR [mine] boom {i}" for i in range(1, 3001)]
    req(store, "PUT", "/v1/files/6.0.0/abc/logs/run.log", pc, ("\n".join(lines) + "\n").encode())
    key = "pcx/6.0.0/abc/logs/run.log"
    code, body = req(store, "GET", f"/api/raw?key={key}", rdr)
    assert code == 200 and body.decode().count("\n") == 3000                      # every line, not a page
    code, body = req(store, "GET", f"/api/raw?key={key}&lvl=1&download=1", rdr)
    assert code == 200 and body.decode().count("\n") == 300 and "ERROR" in body.decode() and "INFO" not in body.decode()
    code, body = req(store, "GET", f"/api/raw?key={key}&q=pr%3D2999", rdr)
    assert code == 200 and body.decode().count("\n") == 1
    assert req(store, "GET", f"/api/raw?key={key}")[0] == 401                  # signing in is required
    assert req(store, "GET", "/api/raw?key=nope/6.0.0/abc/x", rdr)[0] == 404


def test_mined_data_reads_are_computed_once_per_window_and_say_when(tmp_path, monkeypatch):
    monkeypatch.setattr(store_app, "DATA_TTL_S", 600)
    tp = os.path.join(str(tmp_path), "tokens.json")
    rdr = tokens.issue(tp, "readers", "r")
    s = store_app.serve(str(tmp_path), "127.0.0.1", 0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        calls = []
        real = s.service.table_catalogue
        s.service.table_catalogue = lambda *a, **k: (calls.append(1), real(*a, **k))[1]
        url = f"http://127.0.0.1:{s.server_port}/api/tables"
        heads = []
        for _ in range(3):
            r = urllib.request.urlopen(urllib.request.Request(url, headers={"Authorization": f"Bearer {rdr}"}))
            heads.append((r.headers["X-Data-Updated"], r.headers["X-Data-Next"]))
        assert len(calls) == 1, "every reload recomputed the tables"
        assert len(set(heads)) == 1 and int(heads[0][1]) - int(heads[0][0]) == 600
        s.service.data_cache.t -= 601                                               # the window runs out
        urllib.request.urlopen(urllib.request.Request(url, headers={"Authorization": f"Bearer {rdr}"})).read()
        assert len(calls) == 2
    finally:
        s.shutdown()


def test_activity_shows_when_each_pc_mined_and_what_it_is_doing_now(store):
    svc = store.service
    now = time.time()
    h = 3600

    def beat(pc, ago_s, reason, prs, current=None):
        svc.index.db.execute("INSERT INTO activity VALUES (?,?,?,?,?,?,?)", (pc, "6.0.0/abc", now - ago_s, reason, prs, 1, current))
    for ago_s, prs in ((30 * h, 0), (29.9 * h, 10), (29.8 * h, 25)):               # mined 30 hours ago, then went quiet
        beat("pc1", ago_s, "repo done", prs, "o/a")
    beat("pc1", 5 * h, "repo done", 40, "o/a")                                       # and came back
    beat("pc1", max(0, min(60, now % 3600 - 1)), "repo done", 55, "o/b")             # mining right now: a minute ago, inside this hour's block
    beat("pc2", 2 * h, "repo done", 5)
    beat("pc2", 1 * h, "batch done", 9)                                              # finished
    svc.index.db.commit()
    d = svc.activity(7)
    assert d["days"] == 7 and d["step"] == 3600 and len(d["pcs"]) == 2
    pc1, pc2 = d["pcs"]
    assert pc1["now"] == "mining" and pc1["current"] == "o/b" and pc2["now"] == "done"
    states = [b[0] for b in pc1["blocks"]]
    assert states[-1] == "mining" and "offline" in states, "a silent stretch between pushes is offline, not mining"
    assert states[0] == "none", "before a PC was ever seen is not offline"
    assert pc1["prs"] == 55 and 0 < pc1["uptime"] < 1
    assert svc.activity(3)["days"] == 7                                              # an unknown range falls back to 7 days
    code, body = req(store, "GET", "/api/activity?days=1", tokens.issue(store.tp, "readers", "rr"))
    assert code == 200 and json.loads(body)["step"] == 900


def test_the_admin_overview_carries_each_pcs_settings_and_the_splits_model(store):
    pcs = tokens.issue_split(store.tp, "abc", 2, flags=["--max-prs", "20"], version="6.10.10")
    st = {"reason": "repo done", "host": "fedora", "argv": ["--repos-file", "r.txt", "--llm-model", "haiku"], "miner_version": "6.10.10",
          "settings": {"llm": True, "llm_model": "haiku", "llm_backend": "claude_cli", "szz": True, "workers": 6}}
    assert req(store, "PUT", "/v1/files/6.10.10/abc/run.json", pcs[0]["token"], json.dumps(st).encode())[0] == 200
    code, body = req(store, "GET", "/v1/admin/overview", store.adm)
    d = json.loads(body)
    p1 = [p for p in d["pcs"] if p["pc"].startswith("pc1")][0]
    assert p1["settings"]["llm_model"] == "haiku" and p1["argv"][-1] == "haiku" and d["splits"][0]["models"] == ["haiku"]
    assert "retention" not in d, "nothing in the store deletes run logs on a timer"


def test_log_search_filters_by_level_with_or_without_text(store):
    pc = tokens.issue(store.tp, "pcs", "pcl")
    rdr = tokens.issue(store.tp, "readers", "r")
    log = (b"2026-10-21 10:00:00 ERROR [mine] boom\n2026-10-21 10:00:01 WARN [sync] slow\n2026-10-21 10:00:02 INFO [mine] ok\n"
           b"2026-10-21 10:00:03 DEBUG [git] fetch\n[ci] plain\n")
    assert req(store, "PUT", "/v1/files/6.0.0/abc/logs/run.log", pc, log)[0] == 200

    def hits(qs):
        code, body = req(store, "GET", "/api/search?" + qs, rdr)
        assert code == 200, body
        return [(h["level"], h["line"]) for h in json.loads(body)["hits"]]
    assert hits("level=error") == [("error", 1)]                                   # a level alone is a search
    assert hits("level=warn") == [("error", 1), ("warn", 2)]                       # warnings and errors
    assert hits("level=info") == [("info", 3)] and hits("level=debug") == [("debug", 4)]
    assert hits("q=mine&level=warn") == [("error", 1)]                             # text and level together
    assert [h[1] for h in hits("q=plain")] == [5] and hits("q=plain")[0][0] is None
    code, body = req(store, "GET", "/api/search?level=nonsense", rdr)              # an unknown level means any
    assert code == 200
    code, raw = req(store, "GET", "/api/raw?key=pcl/6.0.0/abc/logs/run.log&level=error", rdr)
    assert code == 200 and raw.decode().count("\n") == 1 and "ERROR" in raw.decode()


def test_admin_storage_reports_sizes_by_kind_and_pc_and_the_disk_but_never_data(store):
    pc = tokens.issue(store.tp, "pcs", "pcs1")
    for key, body in (("data/dataset_pcs1.parquet", b"x" * 5000), ("data/dataset_pcs1_snapshots.parquet", b"y" * 3000), ("logs/run.log", b"l" * 700),
                      ("logs/run.trace.jsonl", b"t" * 900), ("health/o_r.json", b"{}")):
        assert req(store, "PUT", f"/v1/files/6.0.0/abc/{key}", pc, body)[0] == 200
    code, body = req(store, "GET", "/v1/admin/storage", store.adm)
    d = json.loads(body)
    assert code == 200 and d["files"] == 5 and d["bytes"] == 5000 + 3000 + 700 + 900 + 2
    kinds = dict(d["by_kind"])
    assert kinds["dataset"] == 5000 and kinds["snapshots"] == 3000 and kinds["logs"] == 700 and kinds["traces"] == 900 and kinds["health"] == 2
    assert d["by_pc"][0][0] == "pcs1" and d["disk"]["free"] > 0 and d["largest"][0]["size"] == 5000
    assert d["estimate"]["storage_month"] >= 0 and "VM" in d["estimate"]["note"]
    assert "content" not in json.dumps(d) and "xxxx" not in json.dumps(d)                       # sizes and names only
    rdr = tokens.issue(store.tp, "readers", "rr")
    assert req(store, "GET", "/v1/admin/storage", rdr)[0] == 401                                  # a read token is not an admin token


def test_an_admin_can_remove_a_split_and_the_page_keeps_a_confirm_open_across_refreshes(store):
    pcs = json.loads(req(store, "POST", "/v1/admin/splits", store.adm, {"split_id": "abc", "n": 2, "flags": ["--max-prs", "20"], "repos_name": "r.txt"})[1])["pcs"]
    code, body = req(store, "POST", "/v1/admin/revoke", store.adm, {"split_id": "abc"})                  # revoke all keeps the record
    assert code == 200 and len(json.loads(body)["revoked"]) == 2 and "abc" in tokens.load(store.tp)["splits"]
    assert tokens.who(store.tp, pcs[0]["token"]) is None
    code, body = req(store, "POST", "/v1/admin/revoke", store.adm, {"split_id": "abc", "remove": True})   # remove takes it off the page
    d = json.loads(body)
    assert code == 200 and d["removed"] is True and "abc" not in tokens.load(store.tp)["splits"]
    assert json.loads(req(store, "GET", "/v1/admin/overview", store.adm)[1])["splits"] == []
    again = json.loads(req(store, "POST", "/v1/admin/revoke", store.adm, {"split_id": "abc", "remove": True})[1])
    assert again["removed"] is False                                                                      # nothing left to remove
    js = open(os.path.join(os.path.dirname(store_app.__file__), "static", "app.js"), encoding="utf-8").read()
    assert "if (pending || editing) return Promise.resolve(lastActive)" in js, "a refresh resets the open confirm and the second press does nothing"
