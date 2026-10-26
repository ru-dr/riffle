"""Command line: `python -m aidev_ingest inventory|check`."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import huggingface_hub
import pyarrow

from . import __version__
from .hub import HubSource
from .inventory import build_inventory, check_plan, compare, summary
from .pin import REPO_ID, REVISION, TAG

DEFAULT_EXPECTED = Path(__file__).resolve().parent.parent / "expected" / f"aidev-{TAG}.inventory.json"


def _meta() -> dict[str, str]:
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tool_version": __version__,
        "huggingface_hub": huggingface_hub.__version__,
        "pyarrow": pyarrow.__version__,
    }


def _warn_if_tag_moved(source: HubSource, revision: str) -> None:
    """The pin is the hash. This only tells you the tag no longer agrees with it."""
    if revision != REVISION:
        return
    current = source.resolve(TAG)
    if current != REVISION:
        print(
            f"note: tag {TAG} now points at {current}; the pin stays on {REVISION}.",
            file=sys.stderr,
        )


def cmd_inventory(args: argparse.Namespace) -> int:
    source = HubSource(args.repo)
    revision = source.resolve(args.revision)
    inventory = build_inventory(source, args.repo, revision)
    _warn_if_tag_moved(source, revision)
    print(summary(inventory), file=sys.stderr)
    problems = check_plan(inventory)
    for line in problems:
        print(f"plan problem: {line}", file=sys.stderr)

    document: dict[str, Any] = {**inventory, "meta": _meta()}
    text = json.dumps(document, indent=2) + "\n"
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"wrote {out}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 1 if problems else 0


def cmd_check(args: argparse.Namespace) -> int:
    expected = json.loads(Path(args.expected).read_text(encoding="utf-8"))
    source = HubSource(expected["repo_id"])
    actual = build_inventory(source, expected["repo_id"], expected["revision"])
    _warn_if_tag_moved(source, expected["revision"])

    differences = compare(expected, actual)
    problems = check_plan(actual)
    for line in differences:
        print(f"DIFFERENT: {line}", file=sys.stderr)
    for line in problems:
        print(f"PLAN: {line}", file=sys.stderr)
    if differences or problems:
        return 1
    print(f"ok: {len(actual['files'])} files at {expected['revision']} match {args.expected}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aidev_ingest", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    inv = sub.add_parser("inventory", help="list files, rows and columns at a revision")
    inv.add_argument("--repo", default=REPO_ID)
    inv.add_argument("--revision", default=REVISION, help="commit hash (default: the pin), tag or branch")
    inv.add_argument("--out", help="write the JSON here instead of stdout")
    inv.set_defaults(func=cmd_inventory)

    chk = sub.add_parser("check", help="verify the hub still matches the committed inventory")
    chk.add_argument("--expected", default=str(DEFAULT_EXPECTED))
    chk.set_defaults(func=cmd_check)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
