"""
progress.py — structured progress events for the TUI (tui.py).

When RIFFLE_EVENTS names a file, every emit() appends one JSON line to it:
{"t": <unix time>, "ev": "<event>", ...fields}. With it unset, emit() is a
no-op, so plain CLI runs are unaffected. Thread-safe; each line is flushed so a
reader tailing the file never sees a partial event.
"""
from __future__ import annotations

import json
import os
import threading
import time

_LOCK = threading.Lock()
_FH = None
_PATH = os.getenv("RIFFLE_EVENTS")


def emit(ev: str, **fields) -> None:
    global _FH
    if not _PATH:
        return
    line = json.dumps({"t": round(time.time(), 3), "ev": ev, **fields}, default=str)
    with _LOCK:
        try:
            if _FH is None:
                _FH = open(_PATH, "a", encoding="utf-8", buffering=1)
            _FH.write(line + "\n")
            _FH.flush()
        except OSError:
            pass
