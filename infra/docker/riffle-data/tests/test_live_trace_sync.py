"""The miner's live trace upload (sync.py): new complete lines are appended on the store; anything unexpected falls back to
sending the whole file, which is always right. A sync never raises."""
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
import sync  # noqa: E402
import tokens  # noqa: E402

PREFIX = "7.0.1/abcd1234"
REL = "logs/run-20261027-100000.trace.jsonl"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.delenv("RIFFLE_LOKI_URL", raising=False)
    store_dir = tmp_path / "store"
    store_dir.mkdir()
    tp = str(store_dir / "tokens.json")
    pc = tokens.issue(tp, "pcs", "pc1")
    srv = store_app.serve(str(store_dir), "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}"
    monkeypatch.setenv("RIFFLE_SYNC_URL", url)
    monkeypatch.setenv("RIFFLE_SYNC_TOKEN", pc)
    monkeypatch.setattr(sync, "_APPEND_CAP", {})
    work = tmp_path / "work"
    work.mkdir()
    trace = work / "run-20261027-100000.trace.jsonl"
    sent = []
    real_put = sync._put

    def spy(url_, token, key, body, size, sha, timeout, extra=None):
        sent.append((key.rsplit("/", 1)[-1], size, dict(extra or {})))
        return real_put(url_, token, key, body, size, sha, timeout, extra)
    monkeypatch.setattr(sync, "_put", spy)

    class E:
        pass
    e = E()
    e.srv, e.trace, e.sent, e.state = srv, trace, sent, str(tmp_path / "state.json")
    e.stored = lambda: open(srv.service.store.local_path(f"pc1/{PREFIX}/{REL}"), "rb").read()

    def run():
        sent.clear()
        return sync.sync(str(work / "dataset.parquet"), PREFIX, {"reason": "heartbeat"}, logs=[str(trace)],
                         state_path=e.state, health_dir=str(work / "health"))
    e.run = run
    yield e
    srv.shutdown()


def appended(e):
    return [s for s in e.sent if s[0].endswith(".trace.jsonl") and "X-Append-Offset" in s[2]]


def whole(e):
    return [s for s in e.sent if s[0].endswith(".trace.jsonl") and "X-Append-Offset" not in s[2]]


def test_first_sync_sends_the_file_then_only_new_lines(env):
    env.trace.write_bytes(b'{"n": 1}\n{"n": 2}\n')
    assert env.run()["failed"] == [] and len(whole(env)) == 1 and not appended(env)
    env.trace.write_bytes(env.trace.read_bytes() + b'{"n": 3}\n')
    assert env.run()["failed"] == []
    assert not whole(env) and appended(env)[0][1:] == (9, {"X-Append-Offset": "18"})     # nine bytes, not the whole file
    assert env.stored() == env.trace.read_bytes()


def test_a_half_written_line_is_left_for_the_next_sync(env):
    env.trace.write_bytes(b'{"n": 1}\n')
    env.run()
    env.trace.write_bytes(env.trace.read_bytes() + b'{"n": 2')
    env.run()
    assert not appended(env) and env.stored() == b'{"n": 1}\n'
    env.trace.write_bytes(env.trace.read_bytes() + b'}\n')
    env.run()
    assert env.stored() == env.trace.read_bytes() == b'{"n": 1}\n{"n": 2}\n'


def test_a_store_without_append_gets_whole_files(env, monkeypatch):
    monkeypatch.setattr(sync, "_store_appends", lambda url, token: False)
    env.trace.write_bytes(b'{"n": 1}\n')
    env.run()
    env.trace.write_bytes(env.trace.read_bytes() + b'{"n": 2}\n')
    env.run()
    assert not appended(env) and len(whole(env)) == 1 and env.stored() == env.trace.read_bytes()


def test_an_offset_the_store_does_not_agree_with_falls_back_to_the_whole_file(env):
    env.trace.write_bytes(b'{"n": 1}\n')
    env.run()
    state = sync._load_state(env.state)
    (store_key,) = state
    state[store_key][f"{PREFIX}/{REL}#off"] = 4                      # the miner's idea of the size is wrong
    sync._save_state(env.state, state)
    env.trace.write_bytes(env.trace.read_bytes() + b'{"n": 2}\n')
    env.run()
    assert len(appended(env)) == 1 and len(whole(env)) == 1          # the append is refused (409), the whole file follows
    assert env.stored() == env.trace.read_bytes()
    env.trace.write_bytes(env.trace.read_bytes() + b'{"n": 3}\n')
    env.run()
    assert not whole(env) and env.stored() == env.trace.read_bytes()  # and appending works again


def test_a_replaced_file_is_sent_whole(env):
    env.trace.write_bytes(b'{"n": 1}\n{"n": 2}\n{"n": 3}\n')
    env.run()
    env.trace.write_bytes(b'{"x": 1}\n')                              # shorter: not the same file
    env.run()
    assert len(whole(env)) == 1 and env.stored() == b'{"x": 1}\n'


def test_nothing_new_sends_nothing(env):
    env.trace.write_bytes(b'{"n": 1}\n')
    env.run()
    r = env.run()
    assert env.sent and all(s[0] == "run.json" for s in env.sent) and r["failed"] == []


def test_an_unreachable_store_is_a_result_not_an_exception(env):
    env.trace.write_bytes(b'{"n": 1}\n')
    env.run()
    env.srv.shutdown()
    env.srv.server_close()
    env.trace.write_bytes(env.trace.read_bytes() + b'{"n": 2}\n')
    r = env.run()
    assert r["failed"] or r["stopped"]


# ---- from the review -------------------------------------------------------------------------------
def test_a_damaged_state_file_never_stops_a_sync_and_is_repaired(env):
    env.trace.write_bytes(b'{"n": 1}\n')
    for bad in (b"\xff\xfe\x00 not utf-8", b'{"http://x#1": []}', b"[1, 2]", b"{"):
        open(env.state, "wb").write(bad)
        r = env.run()                                                 # must not raise
        assert r["failed"] == [] and env.stored() == b'{"n": 1}\n'
    assert isinstance(sync._load_state(env.state), dict)


def test_a_quick_beat_sends_only_the_logs(env, tmp_path):
    work = env.trace.parent
    (work / "dataset.parquet").write_bytes(b"table")
    env.trace.write_bytes(b'{"n": 1}\n')
    env.sent.clear()
    sync.sync(str(work / "dataset.parquet"), PREFIX, {"reason": "heartbeat"}, logs=[str(env.trace)], state_path=env.state,
              health_dir=str(work / "health"), only_logs=True)
    names = {s[0] for s in env.sent}
    assert "run-20261027-100000.trace.jsonl" in names and "dataset.parquet" not in names
    env.sent.clear()
    sync.sync(str(work / "dataset.parquet"), PREFIX, {"reason": "heartbeat"}, logs=[str(env.trace)], state_path=env.state,
              health_dir=str(work / "health"))
    assert "dataset.parquet" in {s[0] for s in env.sent}               # the full beat still sends the tables


def test_a_different_file_under_the_same_name_is_never_spliced_onto_the_old_one(env):
    first = b"".join(b'{"run": "one", "n": %d}\n' % i for i in range(20))
    env.trace.write_bytes(first)
    env.run()
    env.trace.write_bytes(first)                                       # same bytes, so far
    env.trace.write_bytes(b"".join(b'{"run": "TWO", "n": %d}\n' % i for i in range(40)))   # a different, longer file
    env.run()
    assert not appended(env) and len(whole(env)) == 1                  # sent whole, not appended after the old 20 lines
    assert env.stored() == env.trace.read_bytes()
