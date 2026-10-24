"""
sync.py — hand a run's output to the team's central store (ADR 0015).

After each finished repo, on a Stop (ctrl+c) and at the end of a batch,
build_dataset.py calls sync() to PUT what this PC has made to the store: the
run log and trace, the per-repo health files, the dataset, its snapshot and
commit-feature tables, the checkpoint and the shard manifest. It is not
real-time; a PC that is paused or killed hard simply has the state of its last
sync. ci_cache/, snapshots_raw/ and the clones stay on the PC (they are what a
resume reads, and a resume happens on the same machine).

  RIFFLE_SYNC_URL     base URL of the store, e.g. https://riffle-data.example/
  RIFFLE_SYNC_TOKEN   this PC's write-only token (the store maps it to a PC name)

Both unset: sync is off and says nothing. Both are read from .env like the
other secrets. It never fails a run: any error is a warning, a store that
cannot be reached ends that sync early, and a Stop waits at most
RIFFLE_SYNC_TIMEOUT seconds (default 120) for it.

Uploads are incremental. `.sync_state.json` records, per store URL, the size and mtime of each
file as last sent, and an unchanged file is skipped, so syncing often is cheap.
Each PUT carries the file's SHA-256 so the store can reject a torn upload.
"""
from __future__ import annotations

import glob
import hashlib
import http.client
import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

try:
    import fcntl
except ImportError:                                         # Windows: no cross-process lock, the in-process one remains
    fcntl = None

import progress
import config  # noqa: F401  (loads .env, so RIFFLE_SYNC_* are set whoever imports this first)

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, ".sync_state.json")
DEFAULT_BUDGET_S = 120.0
CONNECT_S = 30.0
MAX_FILE_BYTES = 2 * 1024 ** 3          # the store's per-file limit; bigger files are skipped with a warning


def _secure_url(url: str) -> bool:
    """https, or plain http to this machine only (a local store or a test): the token is a bearer secret."""
    u = urllib.parse.urlsplit(url)
    return u.scheme == "https" or (u.scheme == "http" and (u.hostname or "") in ("localhost", "127.0.0.1", "::1"))


def settings() -> tuple[str, str] | None:
    """(base_url, token) when sync is configured, else None. A URL that would send the token in the clear
    (http to another host) is treated as not configured, with a warning."""
    url = (os.environ.get("RIFFLE_SYNC_URL") or "").strip().rstrip("/")
    token = (os.environ.get("RIFFLE_SYNC_TOKEN") or "").strip()
    if url and token and not _secure_url(url):
        print(f"[sync] RIFFLE_SYNC_URL must be https (http is allowed only for localhost); sync is off",
              file=sys.stderr, flush=True)
        return None
    return (url, token) if url and token else None


class _SameHostRedirect(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only within the same host and never downgrade to http: the bearer token must not
    follow a redirect to another origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old, new = urllib.parse.urlsplit(req.full_url), urllib.parse.urlsplit(newurl)
        if (new.scheme, new.netloc) != (old.scheme, old.netloc):
            raise urllib.error.HTTPError(req.full_url, code, f"{msg} (redirect to another origin refused)", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_SameHostRedirect)


def this_run_logs() -> list[str]:
    """This run's trace (RIFFLE_TRACE) and the .log the TUI writes beside it, if
    any. logs/ also holds earlier runs' files; those are not this run's output."""
    trace = os.environ.get("RIFFLE_TRACE") or ""
    if not trace.endswith(".trace.jsonl"):
        return [trace] if os.path.isfile(trace) else []
    return [p for p in (trace[: -len(".trace.jsonl")] + ".log", trace) if os.path.isfile(p)]


def collect(out: str, health_dir: str = "health", logs: list[str] | None = None,
            repos: set[str] | None = None) -> list[tuple[str, str]]:
    """The files worth sending, as (local path, key under the run prefix).
    Only files that exist; the dataset's three tables, checkpoint and shard
    manifest sit beside `out`, and `logs` are this run's log and trace. `repos` (owner/name) limits the
    health files to this run's repos; health/ also holds earlier runs' files."""
    import naming
    snap, commits = naming.snapshot_outs(out)
    stem = os.path.splitext(out)[0]
    candidates = [out, out + ".partial.jsonl", snap, commits, naming.manifest_path(out),
                  stem + ".jsonl"]
    files = [(p, "data/" + os.path.basename(p)) for p in candidates if os.path.isfile(p)]
    files += [(p, "logs/" + os.path.basename(p)) for p in (this_run_logs() if logs is None else logs)
              if os.path.isfile(p)]
    mine = None if repos is None else {r.replace("/", "_").lower() + ".json" for r in repos}
    files += [(p, "health/" + os.path.basename(p))
              for p in sorted(glob.glob(os.path.join(health_dir, "*.json")))
              if mine is None or os.path.basename(p).lower() in mine]
    seen, keys, unique = set(), set(), []
    for p, key in files:                    # `out` and its .jsonl twin can be the same path
        if os.path.abspath(p) in seen:
            continue
        seen.add(os.path.abspath(p))
        if key in keys:                     # two different files with one name would overwrite each other in the store
            print(f"[sync] not sending {p}: another file is already sent as {key}", file=sys.stderr, flush=True)
            continue
        keys.add(key)
        unique.append((p, key))
    return unique


def _load_state(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


_STATE_LOCK = threading.Lock()


def _save_state(path: str, dirty: dict) -> None:
    """Merge `dirty` ({store: {key: signature}}, only what this sync changed) into the file as it is now, under a lock,
    so two syncs (a heartbeat's and a repo's, or two runs) never write each other's older picture back."""
    with _STATE_LOCK:
        lock = None
        try:
            if fcntl is not None:
                lock = open(path + ".lock", "a+")
                fcntl.flock(lock, fcntl.LOCK_EX)
            disk = _load_state(path)
            for store_key, entries in dirty.items():
                disk.setdefault(store_key, {}).update(entries)
            tmp = f"{path}.{os.getpid()}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(disk, fh)
            os.replace(tmp, path)
        except OSError:
            pass
        finally:
            if lock is not None:
                lock.close()


class _OutOfTime(TimeoutError):
    """The whole sync's budget (not one socket call's) ran out while a file was going up."""


def _out_of_time(e: BaseException) -> bool:
    return isinstance(e, _OutOfTime) or isinstance(getattr(e, "reason", None), _OutOfTime)


class _Prefix:
    """The first `n` bytes of a file, so a log that is still growing is sent as
    exactly the bytes that were hashed and counted (the next sync sends the rest)."""

    def __init__(self, fh, n: int, deadline: float | None = None):
        self.fh, self.left, self.deadline = fh, n, deadline

    def read(self, size: int = -1) -> bytes:
        if self.deadline is not None and time.monotonic() > self.deadline:
            raise _OutOfTime("the sync time budget ran out mid-file")
        if size < 0 or size > self.left:
            size = self.left
        data = self.fh.read(size)
        self.left -= len(data)
        return data


def _sha256(path: str, n: int) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        src = _Prefix(fh, n)
        for block in iter(lambda: src.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _put(url: str, token: str, key: str, body, size: int, sha256: str | None, timeout: float) -> None:
    """PUT one object. `body` is bytes or an open file; raises on any failure."""
    req = urllib.request.Request(
        f"{url}/v1/files/{urllib.parse.quote(key)}", data=body, method="PUT",
        headers={"Authorization": f"Bearer {token}", "Content-Length": str(size),
                 "Content-Type": "application/octet-stream",
                 **({"X-Content-SHA256": sha256} if sha256 else {})})
    with _OPENER.open(req, timeout=timeout) as r:
        r.read()


def sync(out: str, prefix: str, status: dict, *, health_dir: str = "health",
         logs: list[str] | None = None, state_path: str = STATE_FILE,
         budget_s: float | None = None, repos: set[str] | None = None,
         max_bytes: int | None = None) -> dict:
    """Send what changed since the last sync under `prefix` (`<miner_version>/<split>`),
    then `run.json` (`status` plus host and time). Returns
    {"sent", "skipped", "failed", "stopped"}; never raises. `max_bytes` leaves out files bigger than
    that (a heartbeat must not start an upload its time budget cannot finish: it would be cut off
    and begin again from zero every time); they go at a repo's end and on Stop."""
    cfg = settings()
    result = {"sent": 0, "skipped": 0, "failed": [], "stopped": None}
    deferred = 0                                            # files left out for size (max_bytes): not sent, not unchanged
    if not cfg:
        result["stopped"] = "off"
        return result
    url, token = cfg
    if budget_s is None:
        try:
            budget_s = float(os.environ.get("RIFFLE_SYNC_TIMEOUT", DEFAULT_BUDGET_S))
        except ValueError:
            budget_s = DEFAULT_BUDGET_S
    deadline = time.monotonic() + budget_s
    all_state = _load_state(state_path)
    # per store AND per token: a machine re-joined as another PC (or a rotated token) sends everything again
    store_key = f"{url}#{hashlib.sha256(token.encode()).hexdigest()[:12]}"
    state = all_state.setdefault(store_key, {})
    dirty: dict = {store_key: {}}
    try:
        files = collect(out, health_dir, logs, repos)
        files.sort(key=lambda f: os.path.getsize(f[0]) if os.path.isfile(f[0]) else 0)   # small first: logs and health never starve behind a big table
    except Exception as e:                                  # noqa: BLE001
        print(f"[sync] could not list files: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        result["stopped"] = "error"
        return result

    for path, rel in files:
        key = f"{prefix}/{rel}"
        try:
            st = os.stat(path)
        except OSError:
            continue
        sig = [st.st_size, st.st_mtime_ns]
        if state.get(key) == sig:
            result["skipped"] += 1
            continue
        if max_bytes is not None and st.st_size > max_bytes:
            result["skipped"] += 1
            deferred += 1
            continue
        if st.st_size > MAX_FILE_BYTES:
            result["failed"].append(f"{rel} (over {MAX_FILE_BYTES >> 30} GiB)")
            continue
        left = deadline - time.monotonic()
        if left <= 0:
            result["stopped"] = "out of time"
            break
        try:
            sha = _sha256(path, st.st_size)
            with open(path, "rb") as fh:
                _put(url, token, key, _Prefix(fh, st.st_size, deadline), st.st_size, sha,
                     min(CONNECT_S + st.st_size / 2e6, left))
            state[key] = sig
            dirty[store_key][key] = sig
            result["sent"] += 1
            _save_state(state_path, dirty)              # a Stop mid-sync must not forget what already went
        except urllib.error.HTTPError as e:
            result["failed"].append(f"{rel} (HTTP {e.code})")
            if e.code in (401, 403):
                result["stopped"] = f"token rejected (HTTP {e.code})"
                break
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException) as e:
            if _out_of_time(e):
                result["stopped"] = "out of time"
                break
            # a store that answers early (401, 413) and closes can look like a reset: ask it before blaming the network
            who = whoami(url, token, timeout=8.0)
            result["failed"].append(f"{rel} ({type(e).__name__})")
            result["stopped"] = (f"token rejected ({who['why']})" if not who["ok"] and not who.get("unreachable")
                                 else "store unreachable")
            break
        except _OutOfTime:
            result["stopped"] = "out of time"
            break
        except OSError as e:                                # a local file that vanished or is locked: skip it, go on
            result["failed"].append(f"{rel} (local {type(e).__name__})")
            continue

    if result["stopped"] in (None, "out of time"):
        if status.get("reason") == "batch done" and (result["failed"] or result["stopped"] or deferred):
            # the store counts only "batch done" as a finished PC: never say it after a partial upload
            status = dict(status, reason="batch incomplete", reason_wanted="batch done")
        doc = dict(status, host=socket.gethostname(), synced_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   files_sent=result["sent"], files_failed=len(result["failed"]),
                   files_deferred=deferred)
        body = json.dumps(doc, indent=2).encode()
        try:
            _put(url, token, f"{prefix}/run.json", body, len(body), None, 10.0)
        except (urllib.error.URLError, OSError, TimeoutError, http.client.HTTPException):
            result["failed"].append("run.json")
    _save_state(state_path, dirty)
    return result


def _get_json(url: str, token: str, path: str, timeout: float, limit: int):
    """GET a small JSON document from the store (at most `limit` bytes). Raises HTTPError, URLError, OSError or ValueError."""
    req = urllib.request.Request(f"{url.rstrip('/')}{path}", headers={"Authorization": f"Bearer {token}"})
    with _OPENER.open(req, timeout=timeout) as r:
        raw = r.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("the answer is larger than expected")
    return json.loads(raw)


_NET_ERRORS = (urllib.error.URLError, OSError, TimeoutError, ValueError, http.client.HTTPException)


def whoami(url: str | None = None, token: str | None = None, timeout: float = 15.0) -> dict:
    """Ask the store who this token is: {"ok": True, "name", "split", "expires", ...} or
    {"ok": False, "why": ...}. Never raises; the join check, the doctor and a run's start use it."""
    cfg = settings() if url is None else (url.rstrip("/"), token or "")
    if not cfg or not cfg[0] or not cfg[1]:
        return {"ok": False, "why": "not configured (RIFFLE_SYNC_URL and RIFFLE_SYNC_TOKEN)", "unset": True}
    try:
        info = _get_json(cfg[0], cfg[1], "/v1/whoami", timeout, 64 * 1024)
        if not isinstance(info, dict):
            raise ValueError("not an object")
        return dict(info, ok=info.get("kind") == "pcs", why=None if info.get("kind") == "pcs" else
                    f"this is a {str(info.get('kind', '?')).rstrip('s')} token, not a PC token")
    except urllib.error.HTTPError as e:
        return {"ok": False, "why": "the store did not accept this token (wrong, revoked or expired)"
                if e.code == 401 else f"the store answered HTTP {e.code}"}
    except _NET_ERRORS as e:
        return {"ok": False, "why": f"cannot reach the store ({type(e).__name__}); check RIFFLE_SYNC_URL", "unreachable": True}


def fetch_split(url: str, token: str, timeout: float = 15.0) -> dict | None:
    """What the store keeps about this token's split (the lead's flags and repos list), or None when it cannot
    be had (an older store, no split, unreachable). Never raises; the caller falls back to the code's own contents."""
    try:
        doc = _get_json(url, token, "/v1/split", timeout, 400 * 1024)
        return doc if isinstance(doc, dict) and doc.get("split_id") else None
    except _NET_ERRORS:
        return None


def put_status(prefix: str, status: dict) -> bool:
    """Send only run.json (no files): the cheap heartbeat between syncs, so the
    store's per-PC progress is never hours stale during a long repo. Never raises."""
    cfg = settings()
    if not cfg:
        return False
    doc = dict(status, host=socket.gethostname(),
               synced_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    body = json.dumps(doc, indent=2).encode()
    try:
        _put(cfg[0], cfg[1], f"{prefix}/run.json", body, len(body), None, 8.0)     # short: it must never hold a run up
        return True
    except (urllib.error.URLError, OSError, TimeoutError, http.client.HTTPException, ValueError):
        return False


def report(r: dict, why: str) -> None:
    """One log line for a finished sync, and an event for the TUI's status line."""
    if r["stopped"] == "off":
        return
    progress.emit("sync", why=why, sent=r["sent"], skipped=r["skipped"], failed=len(r["failed"]),
                  stopped=r["stopped"])
    line = f"[sync] {why}: {r['sent']} sent, {r['skipped']} unchanged"
    if r["failed"]:
        line += f", {len(r['failed'])} failed ({', '.join(r['failed'][:3])}{'...' if len(r['failed']) > 3 else ''})"
    if r["stopped"]:
        line += f"; stopped: {r['stopped']}, the next sync retries"
    print(line, file=sys.stderr if r["failed"] or r["stopped"] else sys.stdout, flush=True)
