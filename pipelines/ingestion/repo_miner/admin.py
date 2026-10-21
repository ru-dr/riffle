"""
admin.py — the lead's view of who may upload (ADR 0015). Uses RIFFLE_SYNC_URL and
RIFFLE_ADMIN_TOKEN from .env; the admin token can issue and revoke PC tokens and reads no data.

  python admin.py list                      every PC token: its split, expiry, last upload
  python admin.py revoke pc2of3-853264b4    cut one PC off at once
  python admin.py revoke-split 853264b4     cut off every PC of a split
  python admin.py whoami                    check the admin token itself

Issuing is part of planning a split: build_dataset.py <flags> --shard-plan N --issue-tokens.
Planning the same split again rotates its tokens (the old ones stop working).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

import config  # noqa: F401  (loads .env)


class AdminError(Exception):
    """A readable reason an admin call failed (the CLI prints it, the TUI shows it)."""


def call(method: str, path: str, body: dict | None = None) -> dict:
    url, token = (os.environ.get("RIFFLE_SYNC_URL") or "").rstrip("/"), os.environ.get("RIFFLE_ADMIN_TOKEN") or ""
    if not url or not token:
        raise AdminError("set RIFFLE_SYNC_URL and RIFFLE_ADMIN_TOKEN in .env (the admin token comes from `manage.py add-admin`)")
    req = urllib.request.Request(url + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise AdminError(f"the store said HTTP {e.code}: {e.read()[:200].decode('utf-8', 'replace')}") from None
    except (urllib.error.URLError, OSError) as e:
        raise AdminError(f"cannot reach the store: {e}") from None


def ago(ts: float | None) -> str:
    if not ts:
        return "never"
    s = max(0, int(time.time() - ts))
    return "just now" if s < 60 else f"{s // 60} min ago" if s < 3600 else f"{s // 3600} h ago" if s < 86400 else f"{s // 86400} d ago"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("whoami")
    sub.add_parser("revoke").add_argument("name")
    sub.add_parser("revoke-split").add_argument("split_id")
    args = ap.parse_args(argv)
    try:
        return run(args)
    except AdminError as e:
        sys.exit(str(e))


def run(args) -> int:
    if args.cmd == "whoami":
        me = call("GET", "/v1/whoami")
        print(f"{me['kind'].rstrip('s')} token '{me['name']}'" + ("" if me["kind"] == "admins" else "  (this is NOT an admin token)"))
    elif args.cmd == "list":
        rows = call("GET", "/v1/admin/pcs")["pcs"]
        if not rows:
            print("no PC tokens issued yet")
        for r in rows:
            exp = time.strftime("%Y-%m-%d", time.gmtime(r["expires"])) if r["expires"] else "never"
            print(f"{r['pc']:26} split {r['split'] or '-':10} expires {exp:11} last upload {ago(r['last_upload'])}")
    elif args.cmd == "revoke":
        gone = call("POST", "/v1/admin/revoke", {"name": args.name, "kind": "pcs"})["revoked"]
        print("revoked: " + (", ".join(gone) if gone else f"nothing named {args.name}"))
    else:
        gone = call("POST", "/v1/admin/revoke", {"split_id": args.split_id})["revoked"]
        print("revoked: " + (", ".join(gone) if gone else f"no tokens for split {args.split_id}"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
