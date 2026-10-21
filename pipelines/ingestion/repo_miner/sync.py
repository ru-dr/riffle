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
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import progress
import config  # noqa: F401  (loads .env, so RIFFLE_SYNC_* are set whoever imports this first)

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, ".sync_state.json")
DEFAULT_BUDGET_S = 120.0
CONNECT_S = 30.0
MAX_FILE_BYTES = 2 * 1024 ** 3          # the store's per-file limit; bigger files are skipped with a warning


def settings() -> tuple[str, str] | None:
    """(base_url, token) when sync is configured, else None."""
    url = (os.environ.get("RIFFLE_SYNC_URL") or "").strip().rstrip("/")
    token = (os.environ.get("RIFFLE_SYNC_TOKEN") or "").strip()
    return (url, token) if url and token else None


def this_run_logs() -> list[str]:
    """This run's trace (RIFFLE_TRACE) and the .log the TUI writes beside it, if
    any. logs/ also holds earlier runs' files; those are not this run's output."""
    trace = os.environ.get("RIFFLE_TRACE") or ""
    if not trace.endswith(".trace.jsonl"):
        return [trace] if os.path.isfile(trace) else []
    return [p for p in (trace[: -len(".trace.jsonl")] + ".log", trace) if os.path.isfile(p)]


def collect(out: str, health_dir: str = "health", logs: list[str] | None = None) -> list[tuple[str, str]]:
    """The files worth sending, as (local path, key under the run prefix).
    Only files that exist; the dataset's three tables, checkpoint and shard
    manifest sit beside `out`, and `logs` are this run's log and trace."""
    import naming
    snap, commits = naming.snapshot_outs(out)
    stem = os.path.splitext(out)[0]
    candidates = [out, out + ".partial.jsonl", snap, commits, naming.manifest_path(out),
                  stem + ".jsonl"]
    files = [(p, "data/" + os.path.basename(p)) for p in candidates if os.path.isfile(p)]
    files += [(p, "logs/" + os.path.basename(p)) for p in (this_run_logs() if logs is None else logs)
              if os.path.isfile(p)]
    files += [(p, "health/" + os.path.basename(p))
              for p in sorted(glob.glob(os.path.join(health_dir, "*.json")))]
    seen, unique = set(), []
    for p, key in files:                    # `out` and its .jsonl twin can be the same path
        if os.path.abspath(p) not in seen:
            seen.add(os.path.abspath(p))
            unique.append((p, key))
    return unique


def _load_state(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(path: str, state: dict) -> None:
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
        os.replace(tmp, path)
    except OSError:
        pass


class _Prefix:
    """The first `n` bytes of a file, so a log that is still growing is sent as
    exactly the bytes that were hashed and counted (the next sync sends the rest)."""

    def __init__(self, fh, n: int):
        self.fh, self.left = fh, n

    def read(self, size: int = -1) -> bytes:
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
    with urllib.request.urlopen(req, timeout=timeout) as r:
        r.read()


def sync(out: str, prefix: str, status: dict, *, health_dir: str = "health",
         logs: list[str] | None = None, state_path: str = STATE_FILE,
         budget_s: float | None = None) -> dict:
    """Send what changed since the last sync under `prefix` (`<miner_version>/<split>`),
    then `run.json` (`status` plus host and time). Returns
    {"sent", "skipped", "failed", "stopped"}; never raises."""
    cfg = settings()
    result = {"sent": 0, "skipped": 0, "failed": [], "stopped": None}
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
    state = all_state.setdefault(url, {})          # per store: a new URL sends everything again
    try:
        files = collect(out, health_dir, logs)
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
                _put(url, token, key, _Prefix(fh, st.st_size), st.st_size, sha,
                     min(CONNECT_S + st.st_size / 2e6, left))
            state[key] = sig
            result["sent"] += 1
        except urllib.error.HTTPError as e:
            result["failed"].append(f"{rel} (HTTP {e.code})")
            if e.code in (401, 403):
                result["stopped"] = f"token rejected (HTTP {e.code})"
                break
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            result["failed"].append(f"{rel} ({type(e).__name__})")
            result["stopped"] = "store unreachable"
            break

    if result["stopped"] in (None, "out of time"):
        doc = dict(status, host=socket.gethostname(), synced_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   files_sent=result["sent"], files_failed=len(result["failed"]))
        body = json.dumps(doc, indent=2).encode()
        try:
            _put(url, token, f"{prefix}/run.json", body, len(body), None, CONNECT_S)
        except (urllib.error.URLError, OSError, TimeoutError):
            result["failed"].append("run.json")
    _save_state(state_path, all_state)
    return result


def whoami(url: str | None = None, token: str | None = None, timeout: float = 15.0) -> dict:
    """Ask the store who this token is: {"ok": True, "name", "split", "expires", ...} or
    {"ok": False, "why": ...}. Never raises; the join check, the doctor and a run's start use it."""
    cfg = settings() if url is None else (url.rstrip("/"), token or "")
    if not cfg or not cfg[0] or not cfg[1]:
        return {"ok": False, "why": "not configured (RIFFLE_SYNC_URL and RIFFLE_SYNC_TOKEN)", "unset": True}
    req = urllib.request.Request(f"{cfg[0]}/v1/whoami", headers={"Authorization": f"Bearer {cfg[1]}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            info = json.loads(r.read())
        return dict(info, ok=info.get("kind") == "pcs", why=None if info.get("kind") == "pcs" else
                    f"this is a {str(info.get('kind', '?')).rstrip('s')} token, not a PC token")
    except urllib.error.HTTPError as e:
        return {"ok": False, "why": "the store did not accept this token (wrong, revoked or expired)"
                if e.code == 401 else f"the store answered HTTP {e.code}"}
    except (urllib.error.URLError, OSError, TimeoutError, ValueError) as e:
        return {"ok": False, "why": f"cannot reach the store ({type(e).__name__})", "unreachable": True}


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
        _put(cfg[0], cfg[1], f"{prefix}/run.json", body, len(body), None, CONNECT_S)
        return True
    except (urllib.error.URLError, OSError, TimeoutError):
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
