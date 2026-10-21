"""
sync.py: incremental PUTs to the team store, bounded and never fatal (ADR 0015).
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
                     health_dir=str(run_dir / "health"),
                     logs=[str(run_dir / "logs" / "run-1.log"), str(run_dir / "logs" / "run-1.trace.jsonl")],
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


def test_status_reports_per_repo_progress(tmp_path, monkeypatch):
    import types
    import build_dataset as bd
    monkeypatch.chdir(tmp_path)
    (tmp_path / "ci_cache").mkdir()
    (tmp_path / "ci_cache" / "o_big.selection.json").write_text(
        json.dumps({"merged": [1] * 70, "unmerged": [1] * 20, "stale": [1] * 10}))
    out = tmp_path / "d_pc1of2.jsonl"
    (tmp_path / "d_pc1of2.jsonl.partial.jsonl").write_text('{"repo":"o/big"}\n' * 40)
    monkeypatch.setattr(bd, "_SYNC", {"args": types.SimpleNamespace(out=str(out), shard="1/2"),
                                      "counts": {}, "repos": {}})
    monkeypatch.setattr(bd.sync, "whoami", lambda *a, **k: {"ok": True, "name": "pc1", "split": None})   # the store check is tested in infra
    entries = [("o", "a", ""), ("o", "big", ""), ("o", "later", "")]
    bd._sync_begin(entries, {"o/a": 25})
    bd._sync_mark("o/big", "mining")
    st = bd._sync_status("heartbeat")
    assert (st["repos_total"], st["repos_done"], st["repos_pending"]) == (3, 1, 1)
    assert st["current"] == "o/big"
    assert st["repos"]["o/big"]["selected"] == 100 and st["repos"]["o/big"]["rows"] == 40
    assert st["prs_done"] == 25 + 40 and st["prs_remaining"] == 60


def test_a_new_store_url_gets_everything_again(store, run_dir, monkeypatch):
    assert _sync(run_dir)["sent"] == 5
    assert _sync(run_dir)["sent"] == 0
    monkeypatch.setenv("RIFFLE_SYNC_URL", os.environ["RIFFLE_SYNC_URL"].rstrip("/") + "/")  # same store, same state
    assert _sync(run_dir)["sent"] == 0
    monkeypatch.setenv("RIFFLE_SYNC_URL", os.environ["RIFFLE_SYNC_URL"].replace("127.0.0.1", "localhost"))
    assert _sync(run_dir)["sent"] == 5


def test_only_this_runs_logs_are_collected(tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    logs.mkdir()
    for n in ("run-1.log", "run-1.trace.jsonl", "run-0.log", "run-0.trace.jsonl"):
        (logs / n).write_text("x")
    monkeypatch.setenv("RIFFLE_TRACE", str(logs / "run-1.trace.jsonl"))
    assert sorted(os.path.basename(p) for p in sync.this_run_logs()) == ["run-1.log", "run-1.trace.jsonl"]
    (logs / "run-1.log").unlink()                                # a command-line run has a trace only
    assert [os.path.basename(p) for p in sync.this_run_logs()] == ["run-1.trace.jsonl"]
    monkeypatch.delenv("RIFFLE_TRACE")
    assert sync.this_run_logs() == []


def test_settings_come_from_dot_env_alone(tmp_path):
    """join.py writes .env, and a run must see it without any exported variable."""
    import subprocess
    (tmp_path / ".env").write_text("RIFFLE_SYNC_URL=https://store.example/\nRIFFLE_SYNC_TOKEN=rw_x\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith("RIFFLE_SYNC") and k != "RIFFLE_NO_DOTENV"}
    env["RIFFLE_DOTENV"] = str(tmp_path / ".env")
    code = f"import sys; sys.path.insert(0, {os.path.dirname(os.path.dirname(os.path.abspath(__file__)))!r}); import sync; print(sync.settings())"
    out = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=env, capture_output=True, text=True).stdout.strip()
    assert out == "('https://store.example', 'rw_x')"


def test_the_store_hears_at_once_when_a_run_and_each_repo_start(monkeypatch):
    """A PC must show on the store within seconds of Start, not after its first repo or a long timer."""
    import types
    import build_dataset as bd
    sent = []
    monkeypatch.setattr(bd.sync, "whoami", lambda *a, **k: {"ok": True, "name": "pc1", "split": None})
    monkeypatch.setattr(bd.sync, "put_status", lambda prefix, status: sent.append((prefix, status["reason"], status.get("current"))) or True)
    monkeypatch.setattr(bd, "_SYNC", {"args": types.SimpleNamespace(out="d.parquet", shard="1/2", split_id="abc"), "counts": {}, "repos": {}})
    assert bd.HEARTBEAT_S == 180
    bd._sync_begin([("o", "a", ""), ("o", "b", "")], {})
    assert [r for _p, r, _c in sent] == ["started"]                                  # the moment the run begins
    bd._sync_mark("o/a", "mining")
    assert sent[-1][1:] == ("repo started", "o/a")                                   # and when a repo begins
    n = len(sent)
    bd._sync_beat()                                                                   # inside the 3-minute window: nothing more
    assert len(sent) == n
    bd._SYNC["beat"] -= bd.HEARTBEAT_S + 1
    bd._sync_beat()
    assert sent[-1][1] == "heartbeat"


def test_the_status_carries_what_the_run_is_set_to_including_the_model(monkeypatch):
    import argparse
    import build_dataset as bd
    ns = bd.build_parser().parse_args(["--llm-model", "haiku", "--max-prs", "20", "--shard", "1/2", "--no-szz"])
    st = bd._run_settings(ns)
    assert st["llm"] is True and st["llm_model"] == "haiku" and st["szz"] is False and st["max_prs"] == 20 and st["shard"] == "1/2"
    assert not any("key" in k or "token" in k for k in st), "no secret belongs in a status"
    off = bd._run_settings(bd.build_parser().parse_args(["--no-llm"]))
    assert off["llm"] is False and off["llm_model"] is None
