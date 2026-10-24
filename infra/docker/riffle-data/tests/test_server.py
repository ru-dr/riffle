"""
The team store: auth, uploads, the pages, search and token handling, plus the
miner's own sync.py talking to it end to end. No network beyond localhost, no GCS.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import threading
import urllib.error
import urllib.request
from urllib.parse import quote

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "server"))
MINER = os.path.join(HERE, "..", "..", "..", "..", "pipelines", "ingestion", "repo_miner")

import app  # noqa: E402
import manage  # noqa: E402
import tokens  # noqa: E402


@pytest.fixture
def srv(tmp_path):
    data = str(tmp_path)
    pc = tokens.issue(os.path.join(data, "tokens.json"), "pcs", "pc1")
    pc2 = tokens.issue(os.path.join(data, "tokens.json"), "pcs", "pc2")
    rd = tokens.issue(os.path.join(data, "tokens.json"), "readers", "lead")
    s = app.serve(data, "127.0.0.1", 0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    s.url, s.pc, s.pc2, s.rd, s.data = f"http://127.0.0.1:{s.server_port}", pc, pc2, rd, data
    yield s
    s.shutdown()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def call(srv, method, path, token=None, body=None, headers=None, basic=False, full=False):
    h = dict(headers or {})
    if token:
        h["Authorization"] = (("Basic " + base64.b64encode(f"x:{token}".encode()).decode()) if basic
                              else f"Bearer {token}")
    req = urllib.request.Request(srv.url + path, data=body, method=method, headers=h)
    try:
        with _opener.open(req, timeout=10) as r:
            return (r.status, r.read(), dict(r.headers)) if full else (r.status, r.read())
    except urllib.error.HTTPError as e:
        return (e.code, e.read(), dict(e.headers)) if full else (e.code, e.read())


def put(srv, key, data: bytes, token=None, sha=True):
    h = {"X-Content-SHA256": hashlib.sha256(data).hexdigest()} if sha else {}
    return call(srv, "PUT", f"/v1/files/{key}", token or srv.pc, data, h)


def test_healthz_open_api_and_pages_need_a_reader(srv):
    assert call(srv, "GET", "/healthz")[0] == 200
    assert call(srv, "GET", "/api/runs")[0] == 401
    code, _, hdr = call(srv, "GET", "/", full=True)
    assert code == 302 and hdr["Location"] == "/login"             # a page sends you to sign in
    assert call(srv, "GET", "/api/runs", srv.pc)[0] == 401          # a PC token cannot read
    assert call(srv, "GET", "/api/runs", srv.rd)[0] == 200
    assert call(srv, "GET", "/api/runs", srv.rd, basic=True)[0] == 200   # scripts can use Basic too


def test_login_sets_a_cookie_that_opens_the_pages(srv):
    code, body = call(srv, "POST", "/login", None, b"token=nope", {"Content-Type": "application/x-www-form-urlencoded"})
    assert code == 401 and b"not accepted" in body
    code, _, hdr = call(srv, "POST", "/login", None, f"token={srv.rd}".encode(), {"Content-Type": "application/x-www-form-urlencoded"}, full=True)
    assert code == 302 and "HttpOnly" in hdr["Set-Cookie"] and "SameSite=Strict" in hdr["Set-Cookie"]
    cookie = hdr["Set-Cookie"].split(";")[0]
    code, page, hdr = call(srv, "GET", "/", None, None, {"Cookie": cookie}, full=True)
    assert code == 200 and b'data-page="overview"' in page
    assert "script-src 'self'" in hdr["Content-Security-Policy"] and hdr["X-Content-Type-Options"] == "nosniff"
    assert b"<script>" not in page                                  # no inline scripts, so the CSP can stay strict
    assert call(srv, "GET", "/api/runs", None, None, {"Cookie": cookie})[0] == 200


def test_login_page_announces_errors_and_has_no_inline_script(srv):
    page = call(srv, "GET", "/login")[1]
    assert b'role="alert"' not in page and b"<script>" not in page and b'for="token"' in page
    bad = call(srv, "POST", "/login", None, b"token=nope", {"Content-Type": "application/x-www-form-urlencoded"})[1]
    assert b'role="alert"' in bad and b"aria-invalid=true" in bad and b"nope" not in bad     # the token is never echoed back


def test_static_files_are_served_and_traversal_is_not(srv):
    assert call(srv, "GET", "/static/riffle.css")[0] == 200
    code, body, hdr = call(srv, "GET", "/static/fonts/Geist-Variable.woff2", full=True)
    assert code == 200 and hdr["Content-Type"].startswith("font/woff2") and len(body) > 10000
    assert call(srv, "GET", "/static/../app.py")[0] == 404
    assert call(srv, "GET", "/static/..%2fapp.py")[0] == 404


def test_failed_sign_ins_are_throttled(srv):
    for i in range(20):                                             # twenty different guesses
        call(srv, "GET", "/api/runs", f"wrong{i}")
    assert call(srv, "GET", "/api/runs", "wrong-again")[0] == 429
    assert call(srv, "GET", "/api/runs", srv.rd)[0] == 200          # the right token still gets in: a NAT neighbour is not locked out


def test_put_needs_a_pc_token_and_stores_under_its_name(srv):
    assert put(srv, "6.6.0/s/logs/a.log", b"x", token="nope")[0] == 401
    assert put(srv, "6.6.0/s/logs/a.log", b"x", token=srv.rd)[0] == 401   # a reader cannot write
    assert put(srv, "6.6.0/s/logs/a.log", b"hello\n")[0] == 200
    assert put(srv, "6.6.0/s/logs/a.log", b"hello\nmore\n", token=srv.pc2)[0] == 200
    assert open(os.path.join(srv.data, "store", "pc1", "6.6.0", "s", "logs", "a.log"), "rb").read() == b"hello\n"
    assert os.path.isfile(os.path.join(srv.data, "store", "pc2", "6.6.0", "s", "logs", "a.log"))


@pytest.mark.parametrize("key", ["../x/y", "a/../../b", "a//b", "onlyone", "a/b c", "a/./b"])
def test_bad_keys_are_refused(srv, key):
    code, _ = call(srv, "PUT", "/v1/files/" + quote(key, safe="/."), srv.pc, b"x")
    assert code in (400, 404)
    assert not os.path.exists(os.path.join(srv.data, "pc1", "x"))


def test_checksum_mismatch_and_short_body_are_refused(srv):
    code, _ = call(srv, "PUT", "/v1/files/6.6.0/s/logs/a.log", srv.pc, b"abc", {"X-Content-SHA256": "0" * 64})
    assert code == 422
    assert not os.path.exists(os.path.join(srv.data, "store", "pc1"))
    assert os.listdir(os.path.join(srv.data, "spool")) == []        # nothing left behind


def test_run_json_feeds_the_run_api(srv):
    status = {"reason": "repo done", "pc": "1/2", "split_id": "abc", "miner_version": "6.6.0", "host": "h1",
              "repos_total": 3, "repos_done": 1, "repos_pending": 1, "repos_failed": 0, "prs_done": 65,
              "prs_remaining": 60, "current": "o/big",
              "repos": {"o/a": {"state": "done", "rows": 25},
                        "o/big": {"state": "mining", "rows": 40, "selected": 100},
                        "o/later": {"state": "pending"}}}
    assert put(srv, "6.6.0/abc/run.json", json.dumps(status).encode())[0] == 200
    assert put(srv, "6.6.0/abc/health/o_a.json", json.dumps({"repo": "o/a", "flags": ["only 25 rows (< 50)"]}).encode())[0] == 200
    runs = json.loads(call(srv, "GET", "/api/runs", srv.rd)[1])
    assert runs[0]["pc"] == "pc1" and runs[0]["status"]["current"] == "o/big"
    d = json.loads(call(srv, "GET", "/api/run?pc=pc1&prefix=6.6.0/abc", srv.rd)[1])
    assert d["health"]["o/a"]["flags"] == ["only 25 rows (< 50)"]
    assert {f["kind"] for f in d["files"]} == {"json"}
    assert call(srv, "GET", "/api/run?pc=pc1&prefix=nope", srv.rd)[0] == 404


def test_search_scans_logs_and_traces(srv):
    put(srv, "6.6.0/abc/logs/run-1.log", b"ok line\n[blame] warning <b>x</b>\n")
    put(srv, "6.6.0/abc/logs/run-1.trace.jsonl", b'{"msg":"blame warning in trace"}\n')
    d = json.loads(call(srv, "GET", "/api/search?q=blame", srv.rd)[1])
    texts = sorted(h["text"] for h in d["hits"])
    assert texts == ['[blame] warning <b>x</b>', '{"msg":"blame warning in trace"}']   # raw text; the page uses textContent
    assert d["pcs"] == []                                            # no run.json yet
    assert "bad regex" in json.loads(call(srv, "GET", "/api/search?q=(&regex=1", srv.rd)[1])["note"]


def test_viewer_apis_for_text_json_jsonl_and_trace(srv):
    put(srv, "6.6.0/abc/logs/run-1.log", b"line one\nWARN two\nline three\n")
    put(srv, "6.6.0/abc/health/o_a.json", b'{"repo":"o/a","stats":{"rows":3},"flags":[]}')
    trace = "\n".join(json.dumps(e) for e in [
        {"lvl": "INFO", "stage": "pr", "msg": "done", "repo": "o/a", "pr": 1, "dur_s": 4.0, "stages": {"git": {"n": 2, "s": 3.0}, "api": {"n": 1, "s": 1.0}}},
        {"lvl": "WARN", "stage": "api", "msg": "retry", "repo": "o/a"}]).encode() + b"\n"
    put(srv, "6.6.0/abc/logs/run-1.trace.jsonl", trace)
    k = "pc1/6.6.0/abc/"
    assert json.loads(call(srv, "GET", f"/api/meta?key={k}logs/run-1.log", srv.rd)[1])["kind"] == "text"
    d = json.loads(call(srv, "GET", f"/api/lines?key={k}logs/run-1.log&q=warn", srv.rd)[1])
    assert d["total"] == 1 and d["lines"] == [[2, "WARN two"]]
    assert json.loads(call(srv, "GET", f"/api/lines?key={k}logs/run-1.log&tail=1&limit=1", srv.rd)[1])["lines"] == [[3, "line three"]]
    assert json.loads(call(srv, "GET", f"/api/json?key={k}health/o_a.json", srv.rd)[1])["stats"]["rows"] == 3
    j = json.loads(call(srv, "GET", f"/api/jsonl?key={k}logs/run-1.trace.jsonl", srv.rd)[1])
    assert j["total"] == 2 and "msg" in j["columns"]
    t = json.loads(call(srv, "GET", f"/api/trace?key={k}logs/run-1.trace.jsonl", srv.rd)[1])
    assert t["prs"] == 1 and t["pr_stage_seconds"][0] == {"l": "git", "v": 3.0, "n": 2} and t["problems"][0]["lvl"] == "WARN"
    assert call(srv, "GET", f"/api/lines?key={k}logs/nope.log", srv.rd)[0] == 404
    assert call(srv, "GET", "/api/lines?key=../../etc/passwd", srv.rd)[0] == 404
    assert call(srv, "GET", f"/api/lines?key={k}logs/run-1.log")[0] == 401


def test_parquet_apis_over_one_file_and_a_table(srv, tmp_path):
    pd = pytest.importorskip("pandas")
    pytest.importorskip("pyarrow")
    pytest.importorskip("duckdb")
    df = pd.DataFrame({"repo": ["o/a"] * 3 + ["o/b"] * 2, "pr": range(5), "score": [0.1, 0.5, 0.9, 0.2, 0.4],
                       "label_bad": [True, False, None, False, True],
                       "created_at": pd.to_datetime(["2026-01-05", "2026-01-20", "2026-02-02", "2026-03-03", "2026-03-04"])})
    path = tmp_path / "d.parquet"
    df.to_parquet(path)
    assert put(srv, "6.6.0/abc/data/dataset_pc1of2.parquet", path.read_bytes())[0] == 200
    assert put(srv, "6.6.0/abc/run.json", json.dumps({"reason": "batch done", "repos": {}}).encode())[0] == 200
    key = "pc1/6.6.0/abc/data/dataset_pc1of2.parquet"

    def get(route, **kw):
        q = "&".join(f"{k}={quote(str(v), safe='/')}" for k, v in kw.items())
        code, body = call(srv, "GET", f"/api/{route}?{q}", srv.rd)
        return code, json.loads(body)

    code, sc = get("parquet/schema", key=key)
    assert code == 200 and sc["rows"] == 5 and {c["name"] for c in sc["columns"]} >= {"repo", "score", "label_bad"}
    code, rows = get("parquet/rows", key=key, sort="score", dir="desc", limit=2)
    assert rows["total"] == 5 and rows["rows"][0][rows["columns"].index("score")] == 0.9
    code, rows = get("parquet/rows", key=key, fcol="repo", fq="o/b")
    assert rows["total"] == 2
    assert get("parquet/rows", key=key, sort="nope")[0] == 400              # column names come from the file
    assert get("parquet/rows", key=key, sort='repo"; drop table x; --')[0] == 400
    code, ch = get("parquet/charts", key=key)
    assert ch["repos"][0] == {"l": "o/a", "v": 3} and ch["labels"][0]["vals"] == [2, 2, 1] and ch["months"]["column"] == "created_at"
    assert get("parquet/profile", key=key, col="score")[1]["kind"] in ("histogram", "top")
    assert get("parquet/profile", key=key, col="created_at")[1]["kind"] == "months"
    cat = json.loads(call(srv, "GET", "/api/tables", srv.rd)[1])
    assert cat["version"] == "6.6.0" and cat["tables"][0] == {"name": "mined_dataset", "finished": 1, "all": 1}
    code, sc = get("parquet/schema", table="mined_dataset", version="6.6.0", split="abc")
    assert code == 200 and sc["rows"] == 5
    assert get("parquet/schema", table="mined_snapshot", version="6.6.0", split="abc")[0] == 404
    assert get("parquet/schema", table="x;y", version="6.6.0", split="abc")[0] == 400
    assert get("parquet/schema", key="pc1/6.6.0/abc/data/missing.parquet")[0] == 404


def test_revoked_token_stops_working_at_once(srv):
    assert put(srv, "6.6.0/abc/logs/a.log", b"x")[0] == 200
    assert tokens.revoke(os.path.join(srv.data, "tokens.json"), "pc1")
    assert put(srv, "6.6.0/abc/logs/a.log", b"y")[0] == 401


def test_tokens_file_holds_only_hashes(srv):
    raw = open(os.path.join(srv.data, "tokens.json")).read()
    assert srv.pc not in raw and srv.rd not in raw and hashlib.sha256(srv.pc.encode()).hexdigest() in raw
    assert oct(os.stat(os.path.join(srv.data, "tokens.json")).st_mode & 0o777) == "0o600"


def test_manage_cli_and_reindex(srv, monkeypatch, capsys):
    monkeypatch.setattr(manage, "DATA", srv.data)
    assert manage.main(["add-pc", "Bad Name"]) == 2
    assert manage.main(["add-pc", "pc3"]) == 0
    assert "rw_" in capsys.readouterr().out
    put(srv, "6.6.0/abc/run.json", b'{"reason":"x"}')
    os.remove(os.path.join(srv.data, "index.db"))
    monkeypatch.setattr(manage, "DATA", srv.data)
    assert manage.main(["reindex"]) == 0


def test_miner_sync_talks_to_the_server(srv, tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(os.path.abspath(MINER))
    import sync
    run = tmp_path / "run"
    (run / "logs").mkdir(parents=True)
    (run / "health").mkdir()
    (run / "logs" / "run-1.log").write_text("mined o/a\n")
    (run / "health" / "o_a.json").write_text('{"repo":"o/a","flags":[]}')
    out = run / "dataset_pc1of2.jsonl"
    out.write_text('{"repo":"o/a"}\n')
    monkeypatch.setenv("RIFFLE_SYNC_URL", srv.url)
    monkeypatch.setenv("RIFFLE_SYNC_TOKEN", srv.pc)
    r = sync.sync(str(out), "6.6.0/abc", {"reason": "repo done", "repos": {}}, health_dir=str(run / "health"),
                  logs=[str(run / "logs" / "run-1.log")], state_path=str(run / "state.json"))
    assert r["failed"] == [] and r["stopped"] is None and r["sent"] == 3
    keys = {f["key"] for f in srv.service.index.files()}
    assert {"pc1/6.6.0/abc/logs/run-1.log", "pc1/6.6.0/abc/health/o_a.json",
            "pc1/6.6.0/abc/data/dataset_pc1of2.jsonl", "pc1/6.6.0/abc/run.json"} <= keys
    assert srv.service.index.run("pc1", "6.6.0/abc")["status"]["reason"] == "repo done"
    assert sync.put_status("6.6.0/abc", {"reason": "heartbeat"})
    assert srv.service.index.run("pc1", "6.6.0/abc")["status"]["reason"] == "heartbeat"
