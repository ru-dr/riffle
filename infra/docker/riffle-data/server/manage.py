"""
manage.py — issue, list and revoke tokens.

  python manage.py add-pc pc1          prints the PC's RIFFLE_SYNC_TOKEN once
  python manage.py add-reader lead     prints a read token (the UI password)
  python manage.py add-admin lead      prints an admin token (issues and revokes PC tokens; reads nothing)
  python manage.py issue-split ID N    N PC tokens bound to split ID, as the miner's --issue-tokens does
  python manage.py revoke-split ID     revoke every PC token of a split
  python manage.py revoke pc1
  python manage.py list
  python manage.py reindex             rebuild the index from the VM's disk

Run it inside the container: docker compose exec riffle-data python manage.py list
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import index
import tokens

DATA = os.environ.get("RIFFLE_DATA", "/data")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for cmd in ("add-pc", "add-reader", "add-admin", "revoke"):
        sub.add_parser(cmd).add_argument("name")
    sp = sub.add_parser("issue-split")
    sp.add_argument("split_id")
    sp.add_argument("n", type=int)
    sp.add_argument("--days", type=float, default=None)
    sub.add_parser("revoke-split").add_argument("split_id")
    sub.add_parser("list")
    sub.add_parser("reindex")
    args = ap.parse_args(argv)
    path = os.path.join(DATA, "tokens.json")
    try:
        if args.cmd in ("add-pc", "add-reader", "add-admin"):
            kind = {"add-pc": "pcs", "add-reader": "readers", "add-admin": "admins"}[args.cmd]
            tok = tokens.issue(path, kind, args.name)
            print(f"{args.name}: {tok}\nShown once; the store keeps only its hash.")
        elif args.cmd == "issue-split":
            for r in tokens.issue_split(path, args.split_id, args.n, args.days):
                print(f"{r['pc']}: {r['token']}")
            print("Shown once; the store keeps only hashes. Send each token only to that PC's owner.")
        elif args.cmd == "revoke-split":
            print("revoked: " + (", ".join(tokens.revoke_split(path, args.split_id)) or "none"))
        elif args.cmd == "revoke":
            print("revoked" if tokens.revoke(path, args.name) else f"no such name: {args.name}")
        elif args.cmd == "list":
            for kind, recs in tokens.load(path).items():
                for name, rec in sorted(recs.items()):
                    extra = (f"  split {rec['split']}" if rec.get("split") else "") + (
                        f"  expires {time.strftime('%Y-%m-%d', time.gmtime(rec['expires']))}" if rec.get("expires") else "")
                    print(f"{kind[:-1]:7} {name:24} created {rec.get('created')}{extra}")
        else:
            print(f"indexed {index.Index(os.path.join(DATA, 'index.db')).rebuild(os.path.join(DATA, 'store'))} files")
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
