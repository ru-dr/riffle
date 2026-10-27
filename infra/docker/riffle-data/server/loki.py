"""
loki.py — the store's one way to Loki (ADR 0017): run traces go in as they arrive, and the search page reads them back.

The miner's trace (runlog.py) is the only logger. The store keeps the file, and forwards each new line to Loki; no
agent tails the disk and nothing else writes to Loki. Off unless RIFFLE_LOKI_URL is set. It never blocks or fails
an upload: lines go on a bounded queue that one thread drains, a Loki that is down is a counter and a message, and
what it missed is sent from the file the next time that file grows (`offsets`, kept in the index database).

  RIFFLE_LOKI_URL      e.g. http://loki:3100
  RIFFLE_GRAFANA_URL   shown on the admin page (optional; a link, never fetched)
  RIFFLE_AIRFLOW_URL   same

Labels (few values each, so Loki stays small): job, pc, miner_version, split, run, lvl, component (the trace's `stage`
field: batch, repo, stage, pr, git, api, heartbeat, ...) and miner_stage (the miner stage's name). Repo and PR number go
in as structured metadata.
"""
from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

MAX_QUEUE = 4000                       # batches waiting; a fuller queue drops the newest and says so
MAX_QUEUE_BYTES = 64 * 1024 ** 2       # and so does more than this much waiting (a count of batches is not a bound on memory)
READ_CHUNK = 1024 * 1024               # bytes of a trace read and queued at once; the worker asks for the next chunk when this one is in
MAX_REQUEST_BYTES = 900_000            # Loki refuses a push over 4 MB (gRPC limit); stay well under it
FUTURE_GRACE_S = 60                    # a line stamped further ahead than this (a PC with a fast clock) is stamped now
# The logging stack's Loki (infra/loki/config.yaml) refuses lines older than reject_old_samples_max_age, 168 h. A trace
# older than that is left out here (counted as `too_old`), not sent for Loki to refuse; RIFFLE_LOKI_MAX_AGE_H follows its config.
try:
    MAX_AGE_NS = int(float(os.environ.get("RIFFLE_LOKI_MAX_AGE_H") or 160) * 3600 * 10 ** 9)
except ValueError:
    MAX_AGE_NS = 160 * 3600 * 10 ** 9
TIMEOUT_S = 8
SEARCH_DAYS = 30
TRACE_KEY = re.compile(r"^(?P<pc>[^/]+)/(?P<version>[^/]+)/(?P<split>[^/]+)/logs/(?P<run>[^/]+)\.trace\.jsonl$")


def is_trace(key: str) -> bool:
    return bool(TRACE_KEY.match(key))


def _ns(stamp) -> int:
    """Nanoseconds since the epoch from a trace `t` (2026-10-27T03:50:01.715Z); now if it cannot be read or is
    ahead of now (Loki rejects a whole push for one line from the future, which would block every later line)."""
    now = time.time_ns()
    try:
        import calendar
        whole, _, frac = str(stamp).rstrip("Z").partition(".")
        secs = calendar.timegm(time.strptime(whole, "%Y-%m-%dT%H:%M:%S"))
        ns = secs * 10 ** 9 + int((frac + "000000000")[:9])
    except (ValueError, OverflowError):
        return now
    return now if ns > now + FUTURE_GRACE_S * 10 ** 9 else ns


def streams_for(key: str, data: bytes, counts: dict | None = None) -> list[dict]:
    """Loki streams for the complete lines in `data` (a slice of the trace at `key`). A torn or foreign line is
    sent as plain text with the run's labels, so nothing the miner wrote is lost; a line older than Loki keeps
    (MAX_AGE_NS) is left out and counted in `counts["too_old"]`."""
    m = TRACE_KEY.match(key)
    if not m:
        return []
    base = {"job": "riffle-miner", "pc": m["pc"], "miner_version": m["version"], "split": m["split"], "run": m["run"]}
    groups: dict[tuple, list] = {}
    for raw in data.split(b"\n"):
        if not raw.strip():
            continue
        text = raw.decode("utf-8", "replace")
        try:
            ev = json.loads(text)
            if not isinstance(ev, dict):
                raise ValueError
        except ValueError:
            ev = {}
        labels = dict(base)
        for label, field in (("lvl", "lvl"), ("component", "stage"), ("miner_stage", "name")):
            v = ev.get(field)
            if isinstance(v, str) and v:
                labels[label] = v[:40]
        meta = {k: str(ev[k]) for k in ("repo", "pr") if ev.get(k) is not None}
        ns = _ns(ev.get("t"))
        if time.time_ns() - ns > MAX_AGE_NS:
            if counts is not None:
                counts["too_old"] = counts.get("too_old", 0) + 1
            continue
        entry = [str(ns), text[:20000]] + ([meta] if meta else [])
        groups.setdefault(tuple(sorted(labels.items())), []).append(entry)
    return [{"stream": dict(k), "values": sorted(v, key=lambda e: int(e[0]))} for k, v in groups.items()]


def chunks(streams: list[dict], limit: int = MAX_REQUEST_BYTES):
    """Push bodies of at most about `limit` bytes each, in time order within every stream."""
    cur, size = {}, 0
    for st in streams:
        key = tuple(sorted(st["stream"].items()))
        for entry in st["values"]:
            n = len(entry[1]) + 120
            if cur and size + n > limit:
                yield {"streams": [{"stream": dict(k), "values": v} for k, v in cur.items()]}
                cur, size = {}, 0
            cur.setdefault(key, []).append(entry)
            size += n
    if cur:
        yield {"streams": [{"stream": dict(k), "values": v} for k, v in cur.items()]}


class LokiSink:
    def __init__(self, url: str | None, index=None, grafana: str = "", airflow: str = "", path_for=None):
        self.url = (url or "").rstrip("/")
        self.index, self.grafana, self.airflow = index, grafana, airflow
        self.path_for = path_for                   # key -> where the stored file is (for the next chunk of a long trace)
        self.q: queue.Queue = queue.Queue(MAX_QUEUE)
        self.queued_bytes = 0
        self.pending: dict[str, int] = {}          # key -> offset up to which lines are queued (not yet known to Loki)
        self.gen: dict[str, int] = {}              # key -> generation; a failed push starts a new one, which voids what was queued
        self.lock = threading.Lock()
        self._ready = (0.0, False)
        self.stats = {"lines": 0, "batches": 0, "dropped": 0, "errors": 0, "rejected": 0, "last_ok": None, "last_error": None}
        if self.url:
            threading.Thread(target=self._drain, name="loki-sink", daemon=True).start()

    def forget(self, key: str) -> None:
        """The stored trace was replaced: what was queued for the old file is void, and Loki's offset is reset by the caller."""
        with self.lock:
            self.gen[key] = self.gen.get(key, 0) + 1
        self.pending.pop(key, None)

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    # ---------------------------------------------------------------- write
    def push(self, key: str, data: bytes, end_offset: int | None = None) -> None:
        """Queue the complete lines of `data` for `key`. `end_offset` is the file offset just after them; it is
        recorded once Loki has them, so a failed push is retried from the file by `catch_up`."""
        if not self.url or not data or not is_trace(key):
            return
        cut = data.rfind(b"\n") + 1
        if not cut:
            return
        end = None if end_offset is None else end_offset - (len(data) - cut)
        with self.lock:
            if self.queued_bytes + cut > MAX_QUEUE_BYTES:
                self.stats["dropped"] += 1             # not recorded as pending: the next catch_up reads these bytes again
                return
            try:
                self.q.put_nowait((key, data[:cut], end, None if end is None else end - cut, self.gen.get(key, 0)))
            except queue.Full:
                self.stats["dropped"] += 1
                return
            self.queued_bytes += cut
            if end is not None:
                self.pending[key] = max(self.pending.get(key, 0), end)

    def catch_up(self, key: str, path: str) -> None:
        """Send what is in the file at `path` beyond the offset Loki is known to have."""
        if not self.url or not is_trace(key):
            return
        start = max(self.offset(key), self.pending.get(key, 0))
        try:
            with open(path, "rb") as fh:
                fh.seek(start)
                data = fh.read(READ_CHUNK)
        except OSError:
            return
        if data:
            self.push(key, data, start + len(data))

    def offset(self, key: str) -> int:
        return self.index.loki_offset(key) if self.index else 0

    def _drain(self) -> None:
        while True:
            key, data, end, start, gen = self.q.get()
            with self.lock:
                self.queued_bytes = max(0, self.queued_bytes - len(data))
            if gen != self.gen.get(key, 0):
                continue                                     # an earlier push of this key failed: these bytes follow the gap, so
            try:                                             # they wait for the next catch_up, which reads from the real offset
                rejected, counts = 0, {}
                for body in chunks(streams_for(key, data, counts)):
                    rejected += self._post_tolerant(body)
                n = data.count(b"\n")
                with self.lock:
                    self.stats.update(last_ok=time.time(), last_error=None)
                    self.stats["lines"] += n - rejected - counts.get("too_old", 0)
                    self.stats["rejected"] += rejected
                    self.stats["too_old"] = self.stats.get("too_old", 0) + counts.get("too_old", 0)
                    self.stats["batches"] += 1
                if end is not None and self.index:
                    if start is None or self.index.loki_offset(key) >= start:       # never record bytes after a gap
                        self.index.set_loki_offset(key, end)
                    self._next_chunk(key)
            except Exception as e:                           # noqa: BLE001  (the sink must never hurt an upload)
                with self.lock:
                    self.gen[key] = self.gen.get(key, 0) + 1
                    self.stats["errors"] += 1
                    self.stats["last_error"] = f"{type(e).__name__}: {e}"[:200]
                self.pending.pop(key, None)                  # the next upload sends these bytes again

    def _next_chunk(self, key: str) -> None:
        """A trace longer than one read chunk is sent a chunk at a time: when this one is in, queue the next."""
        if self.path_for is not None:
            try:
                self.catch_up(key, self.path_for(key))
            except Exception:                                # noqa: BLE001
                pass

    def _post_tolerant(self, body: dict) -> int:
        """Post a body; if Loki rejects it (HTTP 400: one bad entry refuses the whole push) split it until the bad
        entries are alone and drop only those. Returns how many entries were dropped. Other errors raise."""
        try:
            self._post(body)
            return 0
        except urllib.error.HTTPError as e:
            if e.code != 400:
                raise
        entries = [(st["stream"], v) for st in body["streams"] for v in st["values"]]
        if len(entries) <= 1:
            return len(entries)
        mid = len(entries) // 2
        dropped = 0
        for part in (entries[:mid], entries[mid:]):
            grouped: dict[tuple, list] = {}
            for labels, v in part:
                grouped.setdefault(tuple(sorted(labels.items())), []).append(v)
            dropped += self._post_tolerant({"streams": [{"stream": dict(k), "values": v} for k, v in grouped.items()]})
        return dropped

    def _post(self, body: dict) -> None:
        req = urllib.request.Request(self.url + "/loki/api/v1/push", data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            r.read()

    # ---------------------------------------------------------------- read
    def _get(self, path: str, params: dict) -> dict:
        url = f"{self.url}{path}?{urllib.parse.urlencode(params)}"
        with urllib.request.urlopen(url, timeout=TIMEOUT_S) as r:
            return json.loads(r.read(16 * 1024 ** 2))

    def ready(self) -> bool:
        """Whether Loki answers /ready; remembered for 10 s so a polling admin page never waits on a dead Loki."""
        if not self.url:
            return False
        at, was = self._ready
        if time.monotonic() - at < 10:
            return was
        try:
            with urllib.request.urlopen(self.url + "/ready", timeout=2) as r:
                ok = r.status == 200
        except (urllib.error.URLError, OSError, ValueError):
            ok = False
        self._ready = (time.monotonic(), ok)
        return ok

    def search(self, text: str, pc: str, regex: bool, level: str, limit: int) -> list[dict]:
        """Matching trace lines, newest first, as {key, line, text, level} (line is 0: Loki has no line numbers).
        Raises on any failure; the caller falls back to reading the files."""
        if pc and not re.fullmatch(r"[A-Za-z0-9._-]+", pc):
            raise ValueError("not a PC name")                  # the file search answers; never "every PC" by accident
        sel = '{job="riffle-miner"' + (f', pc="{pc}"' if pc else "") + "}"
        if level:
            sel = sel[:-1] + f', lvl="{level.upper()}"' + "}"
        pat = text if regex else re.escape(text)
        q = sel + (f" |~ `(?i){pat.replace(chr(96), '')}`" if text else "")
        end = time.time_ns()
        res = self._get("/loki/api/v1/query_range", {"query": q, "limit": limit, "direction": "backward",
                                                     "start": end - SEARCH_DAYS * 86400 * 10 ** 9, "end": end})
        hits = []
        for s in res.get("data", {}).get("result", []):
            lab = s.get("stream", {})
            key = f"{lab.get('pc')}/{lab.get('miner_version')}/{lab.get('split')}/logs/{lab.get('run')}.trace.jsonl"
            for v in s.get("values", []):
                hits.append((int(v[0]), {"key": key, "line": 0, "text": v[1][:400],
                                         "level": lab.get("lvl", "").lower() or None}))
        hits.sort(key=lambda h: -h[0])
        return [h for _, h in hits[:limit]]

    # ---------------------------------------------------------------- admin
    def status(self) -> dict:
        with self.lock:
            s = dict(self.stats)
        s.update(enabled=self.enabled, url=self.url, queue=self.q.qsize(), ready=self.ready() if self.url else False,
                 grafana=self.grafana, airflow=self.airflow)
        return s
