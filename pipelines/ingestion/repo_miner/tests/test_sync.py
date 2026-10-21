"""
sync.py: incremental PUTs to the team store, bounded and never fatal (ADR 0014).
A real local HTTP server stands in for the store.
"""
from __future__ import annotations

import hashlib
import http.server
import json
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sync  # noqa: E402


class Store(http.server.BaseHTTPRequestHandler):
    puts: dict = {}
    fail_with: int | None = None

    def do_PUT(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        if self.headers.get("Authorization") != "Bearer good":
            return self._reply(401)
        if self.fail_with:
            return self._reply(self.fail_with)
        want = self.headers.get("X-Content-SHA256")
        if want and want != hashlib.sha256(body).hexdigest():
            return self._reply(422)
        Store.puts[self.path.removeprefix("/v1/files/")] = body
        self._reply(200)

    def _reply(self, code):
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *a):
        pass


@pytest.fixture
def store(monkeypatch):
    Store.puts, Store.fail_with = {}, None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Store)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("RIFFLE_SYNC_URL", f"http://127.0.0.1:{srv.server_port}/")
    monkeypatch.setenv("RIFFLE_SYNC_TOKEN", "good")
    yield srv
    srv.shutdown()


@pytest.fixture
def run_dir(tmp_path):
    (tmp_path / "logs").mkdir()
    (tmp_path / "health").mkdir()
    (tmp_path / "logs" / "run-1.log").write_text("hello\n")
    (tmp_path / "logs" / "run-1.trace.jsonl").write_text('{"a":1}\n')
    (tmp_path / "health" / "o_r.json").write_text("{}")
    out = tmp_path / "dataset_pc1of2.jsonl"
    out.write_text('{"repo":"o/r"}\n')
    (tmp_path / "dataset_pc1of2.shard.json").write_text("{}")
    (tmp_path / "ci_cache").mkdir()
    (tmp_path / "ci_cache" / "o_r.json").write_text("{}")     # must never be sent
    return tmp_path


def _sync(run_dir, **kw):
    return sync.sync(str(run_dir / "dataset_pc1of2.jsonl"), "6.6.0/abc", {"reason": "t"},
                     health_dir=str(run_dir / "health"), log_dir=str(run_dir / "logs"),
                     state_path=str(run_dir / "state.json"), **kw)


def test_off_without_settings(monkeypatch, run_dir):
    monkeypatch.delenv("RIFFLE_SYNC_URL", raising=False)
    assert _sync(run_dir)["stopped"] == "off"


def test_sends_run_output_not_the_cache_and_skips_unchanged(store, run_dir):
    r = _sync(run_dir)
    assert not r["failed"] and not r["stopped"]
    keys = set(Store.puts)
    assert "6.6.0/abc/data/dataset_pc1of2.jsonl" in keys
    assert "6.6.0/abc/data/dataset_pc1of2.shard.json" in keys
    assert "6.6.0/abc/logs/run-1.log" in keys and "6.6.0/abc/health/o_r.json" in keys
    assert "6.6.0/abc/run.json" in keys
    assert not any("ci_cache" in k for k in keys)
    assert json.loads(Store.puts["6.6.0/abc/run.json"])["reason"] == "t"

    Store.puts.clear()
    r2 = _sync(run_dir)
    assert r2["sent"] == 0 and r2["skipped"] == 5            # nothing re-sent
    assert set(Store.puts) == {"6.6.0/abc/run.json"}

    (run_dir / "logs" / "run-1.log").write_text("hello\nmore\n")
    Store.puts.clear()
    assert _sync(run_dir)["sent"] == 1
    assert Store.puts["6.6.0/abc/logs/run-1.log"] == b"hello\nmore\n"


def test_bad_token_stops_early(store, run_dir, monkeypatch):
    monkeypatch.setenv("RIFFLE_SYNC_TOKEN", "bad")
    r = _sync(run_dir)
    assert r["sent"] == 0 and "token rejected" in r["stopped"]


def test_server_error_is_reported_and_retried_next_time(store, run_dir):
    Store.fail_with = 500
    r = _sync(run_dir)
    assert r["sent"] == 0 and r["failed"]
    Store.fail_with = None
    assert _sync(run_dir)["failed"] == []
    assert "6.6.0/abc/data/dataset_pc1of2.jsonl" in Store.puts


def test_unreachable_store_never_raises(run_dir, monkeypatch):
    monkeypatch.setenv("RIFFLE_SYNC_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("RIFFLE_SYNC_TOKEN", "good")
    r = _sync(run_dir)
    assert r["stopped"] == "store unreachable" and r["sent"] == 0


def test_time_budget_ends_a_sync(store, run_dir):
    r = _sync(run_dir, budget_s=0)
    assert r["stopped"] == "out of time" and r["sent"] == 0
