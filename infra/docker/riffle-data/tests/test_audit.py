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
        req(store, "GET", "/api/runs", "wrong", headers={"X-Forwarded-For": f"10.9.9.{i}, 203.0.113.7"})
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
