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


def call(srv, method, path, token=None, body=None, headers=None, basic=False):
    h = dict(headers or {})
    if token:
        h["Authorization"] = (("Basic " + base64.b64encode(f"x:{token}".encode()).decode()) if basic
                              else f"Bearer {token}")
    req = urllib.request.Request(srv.url + path, data=body, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def put(srv, key, data: bytes, token=None, sha=True):
    h = {"X-Content-SHA256": hashlib.sha256(data).hexdigest()} if sha else {}
    return call(srv, "PUT", f"/v1/files/{key}", token or srv.pc, data, h)


def test_healthz_open_pages_need_a_reader(srv):
    assert call(srv, "GET", "/healthz")[0] == 200
    assert call(srv, "GET", "/")[0] == 401
    assert call(srv, "GET", "/", srv.pc)[0] == 401                  # a PC token cannot read
    assert call(srv, "GET", "/", srv.rd)[0] == 200
    assert call(srv, "GET", "/", srv.rd, basic=True)[0] == 200      # browser login


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


def test_run_json_feeds_the_overview_and_run_page(srv):
    status = {"reason": "repo done", "pc": "1/2", "split_id": "abc", "miner_version": "6.6.0", "host": "h1",
              "repos_total": 3, "repos_done": 1, "repos_pending": 1, "repos_failed": 0, "prs_done": 65,
              "prs_remaining": 60, "current": "o/big",
              "repos": {"o/a": {"state": "done", "rows": 25},
                        "o/big": {"state": "mining", "rows": 40, "selected": 100},
                        "o/later": {"state": "pending"}}}
    assert put(srv, "6.6.0/abc/run.json", json.dumps(status).encode())[0] == 200
    assert put(srv, "6.6.0/abc/health/o_a.json", json.dumps({"repo": "o/a", "flags": ["only 25 rows (< 50)"]}).encode())[0] == 200
    code, page = call(srv, "GET", "/", srv.rd)
    text = page.decode()
    assert code == 200 and "pc1" in text and "1/3 repos" in text and ">65<" in text and ">60<" in text
    code, page = call(srv, "GET", "/run?pc=pc1&prefix=6.6.0/abc", srv.rd)
    text = page.decode()
    assert code == 200 and "o/big" in text and "only 25 rows" in text and ">60<" in text
    assert json.loads(call(srv, "GET", "/api/runs", srv.rd)[1])[0]["status"]["current"] == "o/big"


def test_search_scans_logs_and_traces_and_escapes_html(srv):
    put(srv, "6.6.0/abc/logs/run-1.log", b"ok line\n[blame] warning <b>x</b>\n")
    put(srv, "6.6.0/abc/logs/run-1.trace.jsonl", b'{"msg":"blame warning in trace"}\n')
    code, page = call(srv, "GET", "/search?q=blame", srv.rd)
    text = page.decode()
    assert code == 200 and "[blame] warning &lt;b&gt;x&lt;/b&gt;" in text and "<b>x</b>" not in text
    assert "blame warning in trace" in text.split("<pre>")[1]       # command-line runs have only a trace
    assert "ok line" not in text
    assert "bad regex" in call(srv, "GET", "/search?q=(&regex=1", srv.rd)[1].decode()


def test_file_view_only_for_small_mirrored_files(srv):
    put(srv, "6.6.0/abc/logs/run-1.log", b"0123456789")
    put(srv, "6.6.0/abc/data/dataset.parquet", b"PAR1")
    assert call(srv, "GET", "/file/pc1/6.6.0/abc/logs/run-1.log?tail=4", srv.rd) == (200, b"6789")
    assert call(srv, "GET", "/file/pc1/6.6.0/abc/data/dataset.parquet", srv.rd)[0] == 404
    assert call(srv, "GET", "/file/pc1/6.6.0/abc/logs/run-1.log")[0] == 401


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
