"""
backup.py — the one piece of state that lives only on the VM: tokens.json (hashes of the tokens,
and the splits' settings). Every change is copied to the bucket, and a new VM with an empty disk
restores it on start. Everything else is rebuilt from the bucket (`manage.py restore`): datasets and
traces are already there, the pages' small files and the index are re-made from it.

A no-op without a bucket. Failures are logged, never raised: a backup problem must not break an
issue or a revoke.
"""
from __future__ import annotations

import os
import time
import threading
import sys

KEY = "_system/tokens.json"             # a PC name cannot start with "_", so no upload can collide with it


_LOCK = threading.Lock()                                    # pushes go one at a time, so a stale copy never lands last
STATE = {"ok": True, "error": None, "at": None}             # shown on the admin overview: a failed copy is not silent
TRIES = 3


def push(store, tokens_path: str, tries: int = TRIES, wait: float = 1.0) -> bool:
    """Copy tokens.json to the bucket, retrying a few times. The latest file is read inside the lock, so the last
    writer's tokens are what the bucket ends up with. A final failure is recorded in STATE."""
    if store._bucket is None or not os.path.isfile(tokens_path):
        return False
    with _LOCK:
        err = None
        for attempt in range(tries):
            try:
                store._bucket.blob(KEY).upload_from_filename(tokens_path)
                STATE.update(ok=True, error=None, at=time.time())
                return True
            except Exception as e:                           # noqa: BLE001
                err = f"{type(e).__name__}: {e}"
                if attempt + 1 < tries:
                    time.sleep(wait * (attempt + 1))
        print(f"[backup] could not copy tokens.json to the bucket after {tries} tries: {err}", file=sys.stderr, flush=True)
        STATE.update(ok=False, error=err, at=time.time())
        return False


def pull(store, tokens_path: str) -> bool:
    """Restore tokens.json from the bucket when this disk has none."""
    if store._bucket is None or os.path.isfile(tokens_path):
        return False
    try:
        blob = store._bucket.blob(KEY)
        if not blob.exists():
            return False
        tmp = tokens_path + ".restore"
        blob.download_to_filename(tmp)
        os.chmod(tmp, 0o600)
        os.replace(tmp, tokens_path)
        print("[backup] restored tokens.json from the bucket", file=sys.stderr, flush=True)
        return True
    except Exception as e:                                   # noqa: BLE001
        print(f"[backup] could not restore tokens.json: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        return False


def restore_all(store, index) -> dict:
    """Rebuild a fresh VM from the bucket: the pages' small files (logs, health, run.json) are copied back
    to the disk and the index is re-made from the listing. Big files stay in the bucket."""
    import json
    import store as store_mod
    if store._bucket is None:
        return {"files": 0, "runs": 0, "mirrored": 0}
    files = runs = mirrored = 0
    for blob in store._bucket.list_blobs():
        key = blob.name
        if key.startswith("_system/") or key.count("/") < 2:
            continue
        index.put_file(key, key.split("/", 1)[0], blob.size or 0, None)
        files += 1
        if store_mod.is_mirrored(key):
            dest = store.local_path(key)
            if not os.path.isfile(dest):
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                blob.download_to_filename(dest)
                mirrored += 1
            if key.endswith("/run.json"):
                try:
                    with open(dest, encoding="utf-8") as fh:
                        index.put_run(key.split("/", 1)[0], key.split("/", 1)[1][: -len("/run.json")], json.load(fh))
                    runs += 1
                except (OSError, ValueError):
                    pass
    return {"files": files, "runs": runs, "mirrored": mirrored}
