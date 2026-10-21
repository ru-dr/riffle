"""
manage.py — issue, list and revoke tokens.

  python manage.py add-pc pc1          prints the PC's RIFFLE_SYNC_TOKEN once
  python manage.py add-reader lead     prints a read token (the UI password)
  python manage.py revoke pc1
  python manage.py list
  python manage.py reindex             rebuild the index from the VM's disk

Run it inside the container: docker compose exec riffle-data python manage.py list
"""
from __future__ import annotations

import argparse
import os
import sys

import index
import tokens

DATA = os.environ.get("RIFFLE_DATA", "/data")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for cmd in ("add-pc", "add-reader", "revoke"):
        sub.add_parser(cmd).add_argument("name")
    sub.add_parser("list")
    sub.add_parser("reindex")
    args = ap.parse_args(argv)
    path = os.path.join(DATA, "tokens.json")
    try:
        if args.cmd in ("add-pc", "add-reader"):
            tok = tokens.issue(path, "pcs" if args.cmd == "add-pc" else "readers", args.name)
            print(f"{args.name}: {tok}\nShown once; the store keeps only its hash.")
        elif args.cmd == "revoke":
            print("revoked" if tokens.revoke(path, args.name) else f"no such name: {args.name}")
        elif args.cmd == "list":
            for kind, recs in tokens.load(path).items():
                for name, rec in sorted(recs.items()):
                    print(f"{kind[:-1]:7} {name:20} created {rec.get('created')}")
        else:
            print(f"indexed {index.Index(os.path.join(DATA, 'index.db')).rebuild(os.path.join(DATA, 'store'))} files")
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
