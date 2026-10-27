"""The miner's trace is the one log: the store keeps it, forwards each new line to Loki, appends live uploads,
searches through Loki and shows its state to the admin. Loki is faked; nothing here may fail an upload."""
from __future__ import annotations

import hashlib
import http.server
import json
import os
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
os.environ.setdefault("RIFFLE_NO_DOCTOR", "1")

import app as store_app  # noqa: E402
import loki  # noqa: E402
import sync  # noqa: E402
import tokens  # noqa: E402

KEY = "7.0.1/abcd1234/logs/run-20261027-100000-pc1of1.trace.jsonl"
FULL = "pc1/" + KEY


def ev(msg="done", **kw):
    return json.dumps({"t": "2026-10-27T10:00:01.500Z", "lvl": "INFO", "stage": "stage", "msg": msg,
                       "name": "szz", "repo": "o/r", **kw})


class FakeLoki(http.server.ThreadingHTTPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0), self.Handler)
        self.pushed, self.up, self.search_result = [], True, {"data": {"result": []}}
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server_port}"

    def lines(self):
        return [v[1] for p in self.pushed for s in p["streams"] for v in s["values"]]

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            if not self.server.up:
                return self.send_error(503)
            doc = json.loads(body)
            bad = getattr(self.server, "reject", None)
            if bad and any(bad in v[1] for st in doc["streams"] for v in st["values"]):
                return self.send_error(400)                     # one bad entry refuses the whole push
            self.server.pushed.append(doc)
            self.send_response(204)
            self.end_headers()

        def do_GET(self):
            if self.path.startswith("/ready"):
                code, body = (200, b"ready") if self.server.up else (503, b"")
            else:
                code, body = 200, json.dumps(self.server.search_result).encode()
            self.send_response(code)
            self.end_headers()
            self.wfile.write(body)


@pytest.fixture
def env(tmp_path, monkeypatch):
    fake = FakeLoki()
    monkeypatch.setenv("RIFFLE_LOKI_URL", fake.url)
    tp = os.path.join(str(tmp_path), "tokens.json")
    adm, pc = tokens.issue(tp, "admins", "maria"), tokens.issue(tp, "pcs", "pc1")
    srv = store_app.serve(str(tmp_path), "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    srv.url, srv.adm, srv.pc, srv.fake = f"http://127.0.0.1:{srv.server_port}", adm, pc, fake
    yield srv
    srv.shutdown()
    fake.shutdown()


def put(srv, key, body, offset=None, sha=True):
    h = {"Authorization": f"Bearer {srv.pc}", "Content-Length": str(len(body))}
    if offset is not None:
        h["X-Append-Offset"] = str(offset)
    if sha:
        h["X-Content-SHA256"] = hashlib.sha256(body).hexdigest()
    req = urllib.request.Request(f"{srv.url}/v1/files/{key}", data=body, method="PUT", headers=h)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def wait(cond, secs=5):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def get_json(srv, path, token):
    req = urllib.request.Request(srv.url + path, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req) as r:
        return json.load(r)


# ---- what goes to Loki -------------------------------------------------------------------------
def test_labels_come_from_the_path_and_the_line():
    streams = loki.streams_for(FULL, (ev("done", pr=7) + "\n").encode())
    assert len(streams) == 1
    lab = streams[0]["stream"]
    assert lab == {"job": "riffle-miner", "pc": "pc1", "miner_version": "7.0.1", "split": "abcd1234",
                   "run": "run-20261027-100000-pc1of1", "lvl": "INFO", "component": "stage", "miner_stage": "szz"}
    ts, line, meta = streams[0]["values"][0]
    assert ts == "1793095201500000000" and json.loads(line)["msg"] == "done" and meta == {"repo": "o/r", "pr": "7"}


def test_a_foreign_line_is_kept_with_the_runs_labels_and_a_non_trace_key_sends_nothing():
    streams = loki.streams_for(FULL, b"not json at all\n\n")
    assert streams[0]["values"][0][1] == "not json at all" and "lvl" not in streams[0]["stream"]
    assert loki.streams_for("pc1/7.0.1/abcd1234/logs/run.log", b"x\n") == []


# ---- ingest ------------------------------------------------------------------------------------
def test_a_whole_file_upload_reaches_loki_and_is_not_sent_twice(env):
    body = (ev("start") + "\n" + ev("done") + "\n").encode()
    assert put(env, KEY, body)[0] == 200
    assert wait(lambda: len(env.fake.lines()) == 2)
    assert put(env, KEY, body)[0] == 200                              # the same file again
    time.sleep(0.4)
    assert len(env.fake.lines()) == 2


def test_an_append_extends_the_file_and_sends_only_the_new_lines(env):
    first = (ev("start") + "\n").encode()
    assert put(env, KEY, first)[0] == 200
    more = (ev("done") + "\n").encode()
    assert put(env, KEY, more, offset=len(first)) == (200, "ok")
    assert wait(lambda: len(env.fake.lines()) == 2)
    path = env.service.store.local_path(FULL)
    assert open(path, "rb").read() == first + more
    assert env.service.index.file(FULL)["size"] == len(first + more)


def test_an_append_at_the_wrong_offset_is_refused_and_says_the_real_size(env):
    put(env, KEY, b'{"a": 1}\n')
    code, msg = put(env, KEY, b'{"b": 2}\n', offset=3)
    assert (code, msg) == (409, "offset 9")
    assert open(env.service.store.local_path(FULL), "rb").read() == b'{"a": 1}\n'      # untouched


def test_append_checks_the_hash_and_only_takes_traces(env):
    assert put(env, KEY, b"x\n", offset=0, sha=False)[0] == 200
    req = urllib.request.Request(f"{env.url}/v1/files/{KEY}", data=b"y\n", method="PUT",
                                 headers={"Authorization": f"Bearer {env.pc}", "Content-Length": "2",
                                          "X-Append-Offset": "2", "X-Content-SHA256": "0" * 64})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req)
    assert e.value.code == 422
    assert put(env, "7.0.1/abcd1234/logs/run.log", b"x\n", offset=0)[0] == 400


def test_whoami_says_this_store_appends(env):
    assert get_json(env, "/v1/whoami", env.pc)["append"] is True


def test_a_loki_that_is_down_never_fails_an_upload_and_gets_what_it_missed(env):
    env.fake.up = False
    body = (ev("start") + "\n").encode()
    assert put(env, KEY, body)[0] == 200                                # the miner's upload is fine
    assert wait(lambda: env.service.loki.status()["last_error"])
    assert env.service.index.loki_offset(FULL) == 0                     # not marked as sent
    env.fake.up = True
    more = (ev("done") + "\n").encode()
    assert put(env, KEY, more, offset=len(body))[0] == 200
    assert wait(lambda: len(env.fake.lines()) == 2)                     # the first line comes with the second
    assert env.service.index.loki_offset(FULL) == len(body + more)


def test_a_half_written_line_waits_for_its_end(env):
    put(env, KEY, b'{"msg": "a"}\n{"msg": "b')
    assert wait(lambda: len(env.fake.lines()) == 1)
    assert env.service.index.loki_offset(FULL) == len(b'{"msg": "a"}\n')
    put(env, KEY, b'{"msg": "a"}\n{"msg": "b"}\n')
    assert wait(lambda: len(env.fake.lines()) == 2)


# ---- reading it back ---------------------------------------------------------------------------
def test_search_reads_loki_and_falls_back_to_the_files(env):
    put(env, KEY, (ev("rate limited") + "\n").encode())
    env.fake.search_result = {"data": {"result": [{"stream": {"pc": "pc1", "miner_version": "7.0.1", "split": "abcd1234",
                                                              "run": "run-x", "lvl": "WARN"},
                                                   "values": [["1793095201500000000", "from loki"]]}]}}
    hits, note = env.service.search("limited", "", False)
    assert hits[0]["text"] == "from loki" and hits[0]["level"] == "warn" and note.startswith("From Loki")
    env.fake.up = False
    env.service.loki.url = "http://127.0.0.1:9"                          # nothing there
    hits, note = env.service.search("limited", "", False)
    assert hits and "rate limited" in hits[0]["text"]                    # the stored file answered


def test_admin_sees_the_state_and_a_pc_does_not(env):
    put(env, KEY, (ev() + "\n").encode())
    assert wait(lambda: env.service.loki.status()["lines"] == 1)
    d = get_json(env, "/v1/admin/tracking", env.adm)
    assert d["enabled"] and d["ready"] and d["lines"] == 1 and d["queue"] == 0 and d["append"] is True
    with pytest.raises(urllib.error.HTTPError) as e:
        get_json(env, "/v1/admin/tracking", env.pc)
    assert e.value.code in (401, 403)


def test_with_no_loki_url_everything_still_works(tmp_path, monkeypatch):
    monkeypatch.delenv("RIFFLE_LOKI_URL", raising=False)
    svc = store_app.Service(str(tmp_path))
    assert not svc.loki.enabled and svc.loki.status()["enabled"] is False
    assert svc.loki.search is not None
    svc.loki.push(FULL, b"x\n", 2)                                       # a no-op, not an error


def test_a_big_backlog_is_sent_in_requests_loki_accepts(env):
    line = ev("done", note="x" * 1900)
    body = ((line + "\n") * 3000).encode()                               # about 6 MB
    assert put(env, KEY, body)[0] == 200
    assert wait(lambda: len(env.fake.lines()) == 3000, 10)
    assert len(env.fake.pushed) > 5
    assert all(len(json.dumps(p)) < 1_100_000 for p in env.fake.pushed)  # Loki's limit is 4 MB; each request stays near 1 MB
    assert env.service.loki.status()["last_error"] is None


def test_chunks_keep_every_line_and_their_order():
    streams = loki.streams_for(FULL, "".join(ev(i=i) + "\n" for i in range(50)).encode())
    out = [v for body in loki.chunks(streams, limit=1500) for s in body["streams"] for v in s["values"]]
    assert len(out) == 50 and [json.loads(v[1])["i"] for v in out] == list(range(50))


# ---- what the reviewer found: ordering, bounds, rejected lines, races -----------------------------
def test_two_appends_at_the_same_offset_are_not_both_taken(env):
    first = (ev("start") + "\n").encode()
    put(env, KEY, first)
    more = (ev("a") + "\n").encode()
    results = []

    def go():
        results.append(put(env, KEY, more, offset=len(first))[0])
    threads = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results) == [200] + [409] * 7                         # one wins; the retries are told the real size
    assert open(env.service.store.local_path(FULL), "rb").read() == first + more      # and no line is doubled
    assert env.service.index.file(FULL)["size"] == len(first + more)


def test_a_failed_push_never_leaves_a_hole_in_loki(env):
    a, b = (ev("a") + "\n").encode(), (ev("b") + "\n").encode()
    env.fake.up = False
    put(env, KEY, a)
    assert wait(lambda: env.service.loki.status()["errors"] >= 1)
    put(env, KEY, a + b)                                                  # Loki still down: the later bytes must not be recorded past the gap
    assert wait(lambda: env.service.loki.status()["errors"] >= 2)
    assert env.service.index.loki_offset(FULL) == 0
    env.fake.up = True
    put(env, KEY, a + b + (ev("c") + "\n").encode())
    assert wait(lambda: len(env.fake.lines()) >= 3)
    time.sleep(0.4)
    got = sorted(json.loads(l)["msg"] for l in env.fake.lines())
    assert got == ["a", "b", "c"]                                       # every line, once
    assert env.service.index.loki_offset(FULL) == len(a + b + (ev("c") + "\n").encode())


def test_the_queue_is_bounded_in_bytes_and_recovers(env, monkeypatch):
    monkeypatch.setattr(loki, "MAX_QUEUE_BYTES", 200)
    sink = env.service.loki
    big = ("".join(ev(i=i) + "\n" for i in range(5))).encode()
    put(env, KEY, big)
    assert wait(lambda: sink.status()["dropped"] >= 1)                  # too many bytes waiting: dropped, said so
    assert env.service.index.loki_offset(FULL) == 0
    monkeypatch.setattr(loki, "MAX_QUEUE_BYTES", 64 * 1024 ** 2)
    put(env, KEY, big + (ev("tail") + "\n").encode())                    # the next upload reads from the real offset
    assert wait(lambda: len(env.fake.lines()) == 6)


def test_a_long_trace_is_sent_a_chunk_at_a_time_without_loss(env, monkeypatch):
    monkeypatch.setattr(loki, "READ_CHUNK", 3000)
    body = ("".join(ev(i=i) + "\n" for i in range(200))).encode()
    put(env, KEY, body)
    assert wait(lambda: len(env.fake.lines()) == 200, 10)
    assert sorted(json.loads(l)["i"] for l in env.fake.lines()) == list(range(200))
    assert env.service.index.loki_offset(FULL) == len(body)


def test_one_line_loki_refuses_does_not_block_the_rest(env):
    env.fake.reject = "POISON"
    body = (ev("a") + "\n" + ev("POISON") + "\n" + ev("b") + "\n").encode()
    put(env, KEY, body)
    assert wait(lambda: len(env.fake.lines()) == 2)
    assert sorted(json.loads(l)["msg"] for l in env.fake.lines()) == ["a", "b"]
    assert wait(lambda: env.service.loki.status()["rejected"] == 1)
    assert env.service.index.loki_offset(FULL) == len(body)             # the trace moves on


def test_a_line_from_the_future_is_stamped_now():
    now = time.time_ns()
    assert abs(loki._ns("2999-01-01T00:00:00.000Z") - now) < 5 * 10 ** 9
    assert loki._ns("2026-10-27T03:50:01.715Z") == 1793073001715000000
    assert abs(loki._ns("garbage") - now) < 5 * 10 ** 9


def test_search_falls_back_to_the_files_when_loki_has_nothing_and_a_bad_pc_is_not_all_pcs(env):
    put(env, KEY, (ev("only in the file", repo="o/zzz") + "\n").encode())
    env.fake.search_result = {"data": {"result": []}}
    hits, note = env.service.search("zzz", "", False)
    assert hits and "zzz" in hits[0]["text"] and not note.startswith("From Loki")
    with pytest.raises(ValueError):
        env.service.loki.search("x", "a/b", False, "", 10)


def test_a_shorter_replacement_of_a_trace_resets_loki_and_a_conflicting_append_is_a_409(env):
    long = (ev("a") + "\n" + ev("b") + "\n" + ev("c") + "\n").encode()
    put(env, KEY, long)
    assert wait(lambda: env.service.index.loki_offset(FULL) == len(long))
    short = (ev("x") + "\n").encode()
    put(env, KEY, short)
    assert wait(lambda: any('"x"' in l or '"msg": "x"' in l for l in env.fake.lines()))
    assert env.service.index.loki_offset(FULL) == len(short)
    assert put(env, KEY + "/deeper.trace.jsonl", b"x\n", offset=0)[0] in (400, 409)


def test_an_append_during_a_bucket_upload_keeps_the_trace_dirty(env, monkeypatch):
    svc = env.service
    put(env, KEY, (ev("a") + "\n").encode())
    svc._unmirrored[FULL] = 1
    released = threading.Event()

    def slow_mirror(key):
        put(env, KEY, (ev("a") + "\n" + ev("b") + "\n").encode(), offset=len((ev("a") + "\n").encode()))   # lands mid-upload
        with svc._mirror_lock:
            pass
        released.set()
    monkeypatch.setattr(svc.store, "mirror", slow_mirror)
    svc.mirror_pending()
    assert released.is_set() and svc._unmirrored.get(FULL, 0) >= 2      # still dirty: the bucket copy lacks line b
