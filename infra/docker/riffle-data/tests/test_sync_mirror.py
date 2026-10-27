"""A PC that runs its own stack still reports to the team store: the miner sends the same upload to an optional second
store (RIFFLE_MIRROR_*). The first store's sync is never touched by the second."""
from __future__ import annotations

import os
import sys
import threading

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
MINER = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", "pipelines", "ingestion", "repo_miner"))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "server"))
sys.path.insert(0, MINER)
os.environ.setdefault("RIFFLE_NO_DOCTOR", "1")

import app as store_app  # noqa: E402
import build_dataset as bd  # noqa: E402
import localstack  # noqa: E402
import sync  # noqa: E402
import tokens  # noqa: E402

PREFIX = "7.0.2/abcd1234"


def _store(tmp_path, name):
    d = tmp_path / name
    d.mkdir()
    pc = tokens.issue(str(d / "tokens.json"), "pcs", "pc1")
    srv = store_app.serve(str(d), "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    srv.url, srv.pc = f"http://127.0.0.1:{srv.server_port}", pc
    return srv


@pytest.fixture
def two(tmp_path, monkeypatch):
    monkeypatch.delenv("RIFFLE_LOKI_URL", raising=False)
    team, local = _store(tmp_path, "team"), _store(tmp_path, "local")
    monkeypatch.setenv("RIFFLE_SYNC_URL", team.url)
    monkeypatch.setenv("RIFFLE_SYNC_TOKEN", team.pc)
    monkeypatch.setenv("RIFFLE_MIRROR_URL", local.url)
    monkeypatch.setenv("RIFFLE_MIRROR_TOKEN", local.pc)
    monkeypatch.setattr(sync, "_APPEND_CAP", {})
    work = tmp_path / "work"
    (work / "logs").mkdir(parents=True)
    (work / "logs" / "run-1.trace.jsonl").write_bytes(b'{"n": 1}\n')

    class W:
        pass
    w = W()
    w.team, w.local, w.work = team, local, work
    w.state = str(tmp_path / "state.json")
    yield w
    team.shutdown()
    local.shutdown()


def _keys(srv):
    return {f["key"].split("/", 1)[1] for f in srv.service.index.files()}


def test_the_mirror_setting_is_read_and_never_equals_the_first(monkeypatch):
    monkeypatch.setenv("RIFFLE_SYNC_URL", "https://team.example")
    monkeypatch.setenv("RIFFLE_SYNC_TOKEN", "a")
    monkeypatch.delenv("RIFFLE_MIRROR_URL", raising=False)
    assert sync.mirror_settings() is None
    monkeypatch.setenv("RIFFLE_MIRROR_URL", "http://127.0.0.1:8470/")
    monkeypatch.setenv("RIFFLE_MIRROR_TOKEN", "b")
    assert sync.mirror_settings() == ("http://127.0.0.1:8470", "b")
    monkeypatch.setenv("RIFFLE_MIRROR_URL", "https://team.example")
    monkeypatch.setenv("RIFFLE_MIRROR_TOKEN", "a")
    assert sync.mirror_settings() is None                            # the same store twice is one store
    monkeypatch.setenv("RIFFLE_MIRROR_URL", "http://elsewhere.example")
    assert sync.mirror_settings() is None                            # a token never travels over http to another host


def test_both_stores_get_the_upload(two):
    status = {"reason": "heartbeat"}
    log = str(two.work / "logs" / "run-1.trace.jsonl")
    for cfg in (None, sync.mirror_settings()):
        r = sync.sync(str(two.work / "d.parquet"), PREFIX, status, logs=[log], state_path=two.state,
                      health_dir=str(two.work / "health"), cfg=cfg)
        assert r["failed"] == []
    assert f"{PREFIX}/logs/run-1.trace.jsonl" in _keys(two.team) and f"{PREFIX}/logs/run-1.trace.jsonl" in _keys(two.local)
    assert f"{PREFIX}/run.json" in _keys(two.team) and f"{PREFIX}/run.json" in _keys(two.local)


def test_the_batch_helper_copies_to_the_mirror_and_survives_a_dead_one(two, monkeypatch):
    class Args:
        out = str(two.work / "d.parquet")
        health_dir = str(two.work / "health")
    monkeypatch.setattr(bd, "_SYNC", {"args": Args, "repos": {}, "counts": {}})
    monkeypatch.setattr(bd, "_sync_prefix", lambda status: PREFIX)
    monkeypatch.setattr(sync, "collect", lambda out, health_dir, logs, repos: [(str(two.work / "logs" / "run-1.trace.jsonl"), "logs/run-1.trace.jsonl")])
    bd._mirror({"reason": "heartbeat"}, files=False)                # a status only
    assert f"{PREFIX}/run.json" in _keys(two.local) and not _keys(two.team)
    bd._mirror({"reason": "heartbeat"}, files=True)                 # files too
    assert f"{PREFIX}/logs/run-1.trace.jsonl" in _keys(two.local)
    two.local.shutdown()
    two.local.server_close()
    bd._mirror({"reason": "heartbeat"}, files=True)                 # a dead mirror: no exception
    bd._mirror({"reason": "heartbeat"}, files=False)


def test_a_joined_pc_mirrors_to_the_local_stack_and_an_unjoined_one_reports_to_it(monkeypatch, tmp_path):
    monkeypatch.setattr(localstack, "ENV_PATH", str(tmp_path / ".env"))
    monkeypatch.setenv(localstack.TOKEN_KEY, "rw_local")
    monkeypatch.setenv("RIFFLE_SYNC_URL", "https://team.example")
    monkeypatch.setenv("RIFFLE_SYNC_TOKEN", "team")
    env = localstack.sync_env()
    assert env == {"RIFFLE_MIRROR_URL": localstack.STORE_URL, "RIFFLE_MIRROR_TOKEN": "rw_local"}    # the team keeps its PC
    monkeypatch.delenv("RIFFLE_SYNC_TOKEN")
    assert localstack.sync_env() == {"RIFFLE_SYNC_URL": localstack.STORE_URL, "RIFFLE_SYNC_TOKEN": "rw_local"}
