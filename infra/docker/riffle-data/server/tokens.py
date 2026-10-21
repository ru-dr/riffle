"""
tokens.py — who may write and who may read (ADR 0014).

tokens.json holds only SHA-256 hashes: {"pcs": {name: {...}}, "readers": {...}}.
A PC's token can PUT files under its own name and nothing else; a reader's token
can read everything. The file is read on every request, so revoking takes effect
at once. A token is shown once, when it is made.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time

NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        data = {}
    return {"pcs": data.get("pcs", {}), "readers": data.get("readers", {})}


def save(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, path)


def issue(path: str, kind: str, name: str) -> str:
    """Make (or replace) a token for a PC ('pcs') or a reader ('readers')."""
    if kind not in ("pcs", "readers") or not NAME.match(name):
        raise ValueError("name must be lowercase letters, digits, - or _ (40 characters at most)")
    data = load(path)
    token = ("rw_" if kind == "pcs" else "rd_") + secrets.token_urlsafe(32)
    data[kind][name] = {"sha256": _hash(token), "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    save(path, data)
    return token


def revoke(path: str, name: str) -> bool:
    data = load(path)
    hit = False
    for kind in ("pcs", "readers"):
        hit = data[kind].pop(name, None) is not None or hit
    if hit:
        save(path, data)
    return hit


def who(path: str, token: str | None) -> tuple[str, str] | None:
    """('pcs' | 'readers', name) for a valid token, else None."""
    if not token:
        return None
    digest = _hash(token)
    data = load(path)
    for kind in ("pcs", "readers"):
        for name, rec in data[kind].items():
            if secrets.compare_digest(rec.get("sha256", ""), digest):
                return kind, name
    return None
