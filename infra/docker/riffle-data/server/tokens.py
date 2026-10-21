"""
tokens.py — who may write, who may read and who may issue tokens (ADR 0015).

tokens.json holds only SHA-256 hashes: {"pcs": {name: {...}}, "readers": {...}, "admins": {...}}.
A PC's token can PUT files under its own name and nothing else; when it was issued
for a split it can write only under that split, and it can expire. A reader's token
can read everything. An admin's token can issue and revoke PC tokens and reads
nothing. The file is read on every request, so revoking takes effect at once. A token
is shown once, when it is made.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time

NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
SPLIT = re.compile(r"^[a-z0-9][a-z0-9_-]{0,23}$")
KINDS = ("pcs", "readers", "admins")
PREFIX = {"pcs": "rw_", "readers": "rd_", "admins": "ad_"}


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        data = {}
    return {k: data.get(k, {}) for k in KINDS}


def save(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, path)


def issue(path: str, kind: str, name: str, *, split: str | None = None, days: float | None = None) -> str:
    """Make (or replace) a token. A PC token may be bound to a split and given a lifetime in days."""
    if kind not in KINDS or not NAME.match(name):
        raise ValueError("name must be lowercase letters, digits, - or _ (40 characters at most)")
    if split is not None and (kind != "pcs" or not SPLIT.match(split)):
        raise ValueError("only a PC token can be bound to a split, and a split is lowercase letters, digits, _ or - (24 at most)")
    data = load(path)
    token = PREFIX[kind] + secrets.token_urlsafe(32)
    rec = {"sha256": _hash(token), "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if split:
        rec["split"] = split
    if days:
        rec["expires"] = int(time.time() + days * 86400)
    data[kind][name] = rec
    save(path, data)
    return token


def issue_split(path: str, split: str, n: int, days: float | None = None) -> list[dict]:
    """One token per PC 1..n for a split, named pc<k>of<n>-<split>. Issuing the same
    split again replaces its tokens, so it also rotates them."""
    if not SPLIT.match(split) or not 1 <= n <= 200:
        raise ValueError("a split ID and 1 to 200 PCs")
    return [{"pc": f"pc{k}of{n}-{split}", "k": k, "n": n,
             "token": issue(path, "pcs", f"pc{k}of{n}-{split}", split=split, days=days)} for k in range(1, n + 1)]


def revoke(path: str, name: str) -> bool:
    data = load(path)
    hit = False
    for kind in KINDS:
        hit = data[kind].pop(name, None) is not None or hit
    if hit:
        save(path, data)
    return hit


def revoke_split(path: str, split: str) -> list[str]:
    data = load(path)
    gone = [n for n, r in data["pcs"].items() if r.get("split") == split]
    for n in gone:
        del data["pcs"][n]
    if gone:
        save(path, data)
    return sorted(gone)


def lookup(path: str, token: str | None) -> dict | None:
    """{kind, name, split, expires} for a valid, unexpired token, else None."""
    if not token:
        return None
    digest = _hash(token)
    data = load(path)
    for kind in KINDS:
        for name, rec in data[kind].items():
            if secrets.compare_digest(rec.get("sha256", ""), digest):
                if rec.get("expires") and rec["expires"] < time.time():
                    return None
                return {"kind": kind, "name": name, "split": rec.get("split"), "expires": rec.get("expires"),
                        "created": rec.get("created")}
    return None


def who(path: str, token: str | None) -> tuple[str, str] | None:
    """('pcs' | 'readers' | 'admins', name) for a valid token, else None."""
    r = lookup(path, token)
    return (r["kind"], r["name"]) if r else None
