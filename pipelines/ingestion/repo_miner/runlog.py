"""
runlog.py — the granular run trace.

The plain run log (logs/run-*.log) stays the readable summary. This module adds
a second, structured record next to it: one JSON object per line in
logs/run-*.trace.jsonl, so a slow or stalled run can be explained afterwards
with jq instead of guessed at.

  {"t": "2026-10-19T23:00:50.123Z", "lvl": "INFO", "stage": "pr", "msg": "done",
   "repo": "vitejs/vite", "pr": 22998, "dur_s": 42.0, "stages": {...}, ...}

Levels (RIFFLE_LOG_LEVEL / --log-level, default INFO):
  DEBUG  every git call, GitHub request and LLM call, and each mine_pr stage
  INFO   run and repo stages with durations, one line per PR with its time
         broken down by stage, quota waits, heartbeats
  WARN   retries, rate-limit waits, slow calls, slow PRs, stalls
  ERROR  failures
WARN and ERROR are also echoed to stderr, so they reach the run log too.

RIFFLE_TRACE names the file; unset, nothing is written (plain CLI runs and
tests are unaffected) and the per-call cost is a timer and a dict update.
Thread-safe; each line is flushed so a reader tailing the file never sees a
partial event. Stage totals per PR are inclusive: a span nested in another
(quota_wait inside llm) counts in both.
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone

LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}

# a single git/API/LLM call this slow is worth a WARN on its own; a PR this slow
# gets its stage breakdown in the WARN; no PR finishing for STALL_S is a stall
SLOW_CALL_S = 30.0
SLOW_PR_S = 300.0
STALL_S = 300.0
HEARTBEAT_S = 120.0

_LOCK = threading.Lock()
_FH = None
_TLS = threading.local()
_ACTIVE: dict[int, dict] = {}          # thread id -> {"pr", "stage", "since"} for the heartbeat
_STATE = {"repo": None, "last_done": time.monotonic(), "level": LEVELS["INFO"], "path": None}
_hb_stop = threading.Event()


def configure(path: str | None = None, level: str | None = None) -> None:
    """Set the trace file and level, and export them so child processes (the
    per-repo miner) inherit them. Either may be None to keep what the
    environment already says."""
    global _FH
    if path:
        os.environ["RIFFLE_TRACE"] = path
    if level:
        os.environ["RIFFLE_LOG_LEVEL"] = level.upper()
    with _LOCK:
        if _FH is not None:
            _FH.close()
            _FH = None
    _STATE["path"] = os.environ.get("RIFFLE_TRACE") or None
    _STATE["level"] = LEVELS.get(os.environ.get("RIFFLE_LOG_LEVEL", "INFO").upper(), LEVELS["INFO"])


configure()


def enabled(level: str = "DEBUG") -> bool:
    return bool(_STATE["path"]) and LEVELS[level] >= _STATE["level"]


def set_repo(repo: str | None) -> None:
    _STATE["repo"] = repo


def log(level: str, stage: str, msg: str, /, **fields) -> None:
    """One event. WARN/ERROR echo to stderr even when no trace file is set."""
    lvl = LEVELS[level]
    if lvl >= LEVELS["WARN"]:
        extra = " ".join(f"{k}={v}" for k, v in fields.items() if not isinstance(v, (dict, list)))
        print(f"[{'warn' if level == 'WARN' else 'error'}] {stage}: {msg}" + (f" ({extra})" if extra else ""),
              file=sys.stderr, flush=True)
    if not _STATE["path"] or lvl < _STATE["level"]:
        return
    rec = {"t": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
           "lvl": level, "stage": stage, "msg": msg}
    if _STATE["repo"]:
        rec["repo"] = _STATE["repo"]
    pr = getattr(_TLS, "pr", None)
    if pr is not None:
        rec["pr"] = pr
    rec.update(fields)
    _write(json.dumps(rec, default=str))


def _write(line: str) -> None:
    global _FH
    with _LOCK:
        try:
            if _FH is None:
                _FH = open(_STATE["path"], "a", encoding="utf-8", buffering=1)
            _FH.write(line + "\n")
        except OSError:
            pass


def debug(stage, msg, /, **f): log("DEBUG", stage, msg, **f)   # noqa: E704
def info(stage, msg, /, **f): log("INFO", stage, msg, **f)     # noqa: E704
def warn(stage, msg, /, **f): log("WARN", stage, msg, **f)     # noqa: E704
def error(stage, msg, /, **f): log("ERROR", stage, msg, **f)   # noqa: E704


_STAGE = {"name": None, "t0": 0.0}


def _fmt(secs: float) -> str:
    m, sec = divmod(int(round(secs)), 60)
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{sec:02d}s" if m else f"{sec}s"


def stage(name: str | None = None, **fields) -> None:
    """Mark the start of a run stage, ending the previous one: no re-indenting
    of straight-line code needed. The run log gets `[stage] X done in 2m57s` (so
    a quiet stretch has a name and a length) and the trace gets start/done events.
    stage(None) ends the current stage without starting another."""
    now = time.monotonic()
    with _LOCK:
        prev, t0 = _STAGE["name"], _STAGE["t0"]
        _STAGE["name"], _STAGE["t0"] = name, now
    if prev:
        dur = now - t0
        if dur >= 1:
            print(f"[stage] {prev} done in {_fmt(dur)}", file=sys.stderr, flush=True)
        info("stage", "done", name=prev, dur_s=round(dur, 2))
    if name:
        info("stage", "start", name=name, **fields)


def _acc(stage: str, secs: float) -> None:
    acc = getattr(_TLS, "stages", None)
    if acc is not None:
        slot = acc.setdefault(stage, [0, 0.0])
        slot[0] += 1
        slot[1] += secs


@contextlib.contextmanager
def span(stage: str, msg: str = "", level: str = "DEBUG", slow_s: float | None = None,
         err_level: str = "ERROR", **fields):
    """Time a block. Adds to the current PR's stage totals always; writes an
    event at `level` (and a WARN past `slow_s`) when it ends. A failing block is
    logged at `err_level` with the exception type, then re-raised; callers that
    expect and handle the exception (HTTP errors) pass a quieter level."""
    t0 = time.monotonic()
    me = _ACTIVE.get(threading.get_ident())
    prev = me["stage"] if me else None
    if me:
        me["stage"] = stage
    try:
        yield
    except BaseException as e:
        dur = round(time.monotonic() - t0, 3)
        log(err_level, stage, msg or "failed", dur_s=dur, exc=type(e).__name__, **fields)
        raise
    else:
        dur = time.monotonic() - t0
        if slow_s is not None and dur >= slow_s:
            warn(stage, f"slow: {msg}".strip(": "), dur_s=round(dur, 1), **fields)
        else:
            log(level, stage, msg or "done", dur_s=round(dur, 3), **fields)
    finally:
        _acc(stage, time.monotonic() - t0)
        if me:
            me["stage"] = prev


def step(name: str | None = None) -> None:
    """Straight-line markers inside one PR's mining: step("history") ends the
    previous step (adding its time to this PR's totals as `pr.<name>`) and starts
    this one; step(None) ends the last. A no-op outside a pr_scope."""
    cur = getattr(_TLS, "step", None)
    now = time.monotonic()
    if cur is not None:
        _acc("pr." + cur[0], now - cur[1])
        debug("pr", "step", name=cur[0], dur_s=round(now - cur[1], 3))
    _TLS.step = (name, now) if name and getattr(_TLS, "stages", None) is not None else None
    me = _ACTIVE.get(threading.get_ident())
    if me and name:
        me["stage"] = "pr." + name


def set_status(status: str) -> None:
    """Outcome of the PR in the current scope (default "ok"), for its done line."""
    _TLS.status = status


@contextlib.contextmanager
def pr_scope(num, waited=lambda: 0.0):
    """Bind PR `num` to this thread for the PR's duration, collect its stage
    totals, and write its one INFO line (WARN past SLOW_PR_S) when it ends.
    `waited` returns the seconds spent in quota pauses so far."""
    _TLS.pr, _TLS.stages, _TLS.step, _TLS.status = num, {}, None, "ok"
    _ACTIVE[threading.get_ident()] = {"pr": num, "stage": None, "since": time.monotonic()}
    t0, w0 = time.monotonic(), waited()
    try:
        yield
    except BaseException:
        _TLS.status = "error"
        raise
    finally:
        step(None)
        status = _TLS.status
        dur = time.monotonic() - t0
        stages = {k: {"n": n, "s": round(s, 2)} for k, (n, s) in
                  sorted(_TLS.stages.items(), key=lambda kv: -kv[1][1])}
        _ACTIVE.pop(threading.get_ident(), None)
        _STATE["last_done"] = time.monotonic()
        fields = dict(dur_s=round(dur, 2), quota_wait_s=round(waited() - w0, 1), status=status, stages=stages)
        if dur - (waited() - w0) >= SLOW_PR_S:
            top = ", ".join(f"{k} {v['s']:.0f}s" for k, v in list(stages.items())[:3])
            warn("pr", f"slow PR, top stages: {top}", dur_s=round(dur, 1))
        log("INFO", "pr", "done", **fields)
        _TLS.pr = _TLS.stages = None


def start_heartbeat() -> None:
    """Every HEARTBEAT_S write what each worker is doing; if no PR has finished
    for STALL_S, WARN with the longest-running in-flight PRs. This is what turns
    an unexplained gap in the run log into a named stage."""
    if not _STATE["path"] or getattr(start_heartbeat, "_t", None):
        return

    def loop():
        while not _hb_stop.wait(HEARTBEAT_S):
            now = time.monotonic()
            flight = sorted(({"pr": a["pr"], "stage": a["stage"], "for_s": round(now - a["since"])}
                             for a in list(_ACTIVE.values())), key=lambda a: -a["for_s"])
            if not flight:
                continue
            quiet = now - _STATE["last_done"]
            if quiet >= STALL_S:
                warn("heartbeat", f"no PR finished for {quiet:.0f}s", in_flight=flight[:6])
            else:
                info("heartbeat", "alive", since_last_pr_s=round(quiet), in_flight=flight[:6])

    t = threading.Thread(target=loop, name="runlog-heartbeat", daemon=True)
    start_heartbeat._t = t
    t.start()
