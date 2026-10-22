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

import contextlib
import hashlib
import json
import os
import re
import secrets
import tempfile
import threading
import time

try:
    import fcntl
except ImportError:                                         # Windows: the store runs on Linux; tests there still import
    fcntl = None

NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}\Z")
SPLIT = re.compile(r"^[a-z0-9][a-z0-9_-]{0,23}\Z")
KINDS = ("pcs", "readers", "admins")
PREFIX = {"pcs": "rw_", "readers": "rd_", "admins": "ad_"}


_TLOCK = threading.RLock()
_depth = 0
MAX_DAYS = 3650


@contextlib.contextmanager
def locked(path: str):
    """One change to tokens.json at a time, across the server's threads and across processes (manage.py runs beside the
    server). Re-entrant, so a function that holds it may call another that takes it. Readers need no lock: save() replaces
    the file in one step."""
    global _depth
    with _TLOCK:
        first = _depth == 0
        fh = None
        if first and fcntl is not None:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            fh = open(path + ".lock", "a+")
            fcntl.flock(fh, fcntl.LOCK_EX)
        _depth += 1
        try:
            yield
        finally:
            _depth -= 1
            if fh is not None:
                fcntl.flock(fh, fcntl.LOCK_UN)
                fh.close()


def _days(days) -> float | None:
    """None or 0 means no expiry; otherwise a number of days from just over zero to ten years."""
    if days in (None, 0, ""):
        return None
    try:
        d = float(days)
    except (TypeError, ValueError):
        raise ValueError("days must be a number") from None
    if not 0 < d <= MAX_DAYS:
        raise ValueError(f"days must be more than 0 and at most {MAX_DAYS} (leave it out for no expiry)")
    return d


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def load(path: str, strict: bool = False) -> dict:
    """strict (every change to the file): a file that exists but cannot be read is an error, never an empty
    set that the next save would write over every token."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError("not an object")
    except FileNotFoundError:
        data = {}
    except (OSError, ValueError):
        if strict:
            raise RuntimeError(f"{path} exists but cannot be read; not changing it (restore it with manage.py restore)") from None
        data = {}
    out = {k: data.get(k, {}) for k in KINDS}
    out["splits"] = data.get("splits", {})                  # split ID -> {n, flags, version, created, days}
    return out


def save(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".tokens-", suffix=".tmp")   # private (0600), unique
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def issue(path: str, kind: str, name: str, *, split: str | None = None, days: float | None = None) -> str:
    """Make (or replace) a token. A PC token may be bound to a split and given a lifetime in days."""
    if kind not in KINDS or not NAME.match(name):
        raise ValueError("name must be lowercase letters, digits, - or _ (40 characters at most)")
    if split is not None and (kind != "pcs" or not SPLIT.match(split)):
        raise ValueError("only a PC token can be bound to a split, and a split is lowercase letters, digits, _ or - (24 at most)")
    days = _days(days)
    with locked(path):
        return _issue_locked(path, kind, name, split, days)


def _issue_locked(path: str, kind: str, name: str, split: str | None, days: float | None) -> str:
    data = load(path, strict=True)
    token = PREFIX[kind] + secrets.token_urlsafe(32)
    rec = {"sha256": _hash(token), "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if split:
        rec["split"] = split
    if days:
        rec["expires"] = int(time.time() + days * 86400)
    data[kind][name] = rec
    save(path, data)
    return token


REPOS_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z")
MAX_REPOS_TEXT = 200_000


def issue_split(path: str, split: str, n: int, days: float | None = None, flags: list[str] | None = None,
                version: str | None = None, repos_name: str | None = None, repos_text: str | None = None,
                issued_by: str | None = None) -> list[dict]:
    """One token per PC 1..n for a split, named pc<k>of<n>-<split>. Issuing the same split
    again replaces its tokens, so it also rotates them. The split's flags and miner version are
    kept (not secret) so the admin page can rotate it and rebuild the join codes later."""
    if not SPLIT.match(split) or not 1 <= n <= 200:
        raise ValueError("a split ID and 1 to 200 PCs")
    if repos_name is not None and not REPOS_NAME.match(repos_name):
        raise ValueError("repos_name must be a plain file name")
    if repos_text is not None and (not isinstance(repos_text, str) or len(repos_text) > MAX_REPOS_TEXT):
        raise ValueError(f"repos_text must be text of at most {MAX_REPOS_TEXT} characters")
    days = _days(days)
    with locked(path):
        return _issue_split_locked(path, split, n, days, flags, version, repos_name, repos_text, issued_by)


def _issue_split_locked(path, split, n, days, flags, version, repos_name, repos_text, issued_by) -> list[dict]:
    data = load(path, strict=True)
    stale = [nm for nm, rec in data["pcs"].items() if rec.get("split") == split]
    if stale:                                               # a re-issue with another N must not leave the old tokens valid
        for nm in stale:
            del data["pcs"][nm]
        save(path, data)
    out = [{"pc": f"pc{k}of{n}-{split}", "k": k, "n": n,
            "token": issue(path, "pcs", f"pc{k}of{n}-{split}", split=split, days=days)} for k in range(1, n + 1)]
    data = load(path, strict=True)
    old = data["splits"].get(split, {})
    data["splits"][split] = {"n": n, "days": days, "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                             "flags": [str(f) for f in flags] if flags is not None else old.get("flags", []),
                             "version": version or old.get("version"),
                             "repos_name": repos_name or old.get("repos_name"),
                             "repos_text": repos_text if repos_text is not None else old.get("repos_text"),
                             "issued_by": issued_by or old.get("issued_by")}
    save(path, data)
    return out


def revoke_kind(path: str, kind: str, name: str) -> bool:
    """Revoke one name of one kind (revoke() removes the name from every kind)."""
    with locked(path):
        if kind not in KINDS:
            return False
        data = load(path, strict=True)
        hit = data[kind].pop(name, None) is not None
        if hit:
            save(path, data)
        return hit


def revoke(path: str, name: str) -> bool:
    with locked(path):
        data = load(path, strict=True)
        hit = False
        for kind in KINDS:
            hit = data[kind].pop(name, None) is not None or hit
        if hit:
            save(path, data)
        return hit


def revoke_split(path: str, split: str) -> list[str]:
    with locked(path):
        data = load(path, strict=True)
        gone = [n for n, r in data["pcs"].items() if r.get("split") == split]
        for n in gone:
            del data["pcs"][n]
        if gone:
            save(path, data)
        return sorted(gone)


def remove_split(path: str, split: str) -> bool:
    """Forget a split's record (its flags, repo list and who issued it). Its PC tokens are revoked first, so nothing is left
    that could upload under it, and it cannot be rotated again."""
    with locked(path):
        data = load(path, strict=True)
        for n in [n for n, r in data["pcs"].items() if r.get("split") == split]:
            del data["pcs"][n]
        hit = data["splits"].pop(split, None) is not None
        save(path, data)
        return hit


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
