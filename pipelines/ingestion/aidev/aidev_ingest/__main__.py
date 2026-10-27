"""Command line: `python -m aidev_ingest inventory|check|download|verify|closure`."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import huggingface_hub
import pyarrow

from . import __version__
from .closure import format_report, pop_closure
from .download import DownloadError, download_tables
from .hub import HubSource
from .inventory import build_inventory, check_plan, compare, summary
from .manifest import ManifestError, record_check, verify_dir
from .pin import REPO_ID, REVISION, TAG, as_is_tables

DEFAULT_EXPECTED = Path(__file__).resolve().parent.parent / "expected" / f"aidev-{TAG}.inventory.json"


def _default_out() -> str | None:
    """data/raw/aidev at the repository root, which .gitignore covers.

    Only when this package sits in a checkout. Anywhere else the caller must say
    where, because a default relative to the working directory could land outside
    an ignored folder.
    """
    root = Path(__file__).resolve().parents[4]
    return str(root / "data" / "raw" / "aidev") if (root / ".git").exists() else None


def _inventory_and_out(args: argparse.Namespace) -> tuple[dict[str, Any], Path] | None:
    out = args.out or _default_out()
    if out is None:
        print("error: --out is required outside a repository checkout", file=sys.stderr)
        return None
    inventory = json.loads(Path(args.expected).read_text(encoding="utf-8"))
    # The inventory is a committed file. Its repo and revision decide what is fetched and where
    # it is written, so check them instead of trusting them.
    if inventory.get("repo_id") != REPO_ID:
        print(f"error: the inventory is for {inventory.get('repo_id')!r}, not {REPO_ID!r}", file=sys.stderr)
        return None
    if not re.fullmatch(r"[0-9a-f]{40}", str(inventory.get("revision", ""))):
        print("error: the inventory revision is not a 40-character commit hash", file=sys.stderr)
        return None
    return inventory, Path(out)


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


def cmd_download(args: argparse.Namespace) -> int:
    loaded = _inventory_and_out(args)
    if loaded is None:
        return 2
    inventory, out = loaded
    tables = [t.strip() for t in args.tables.split(",") if t.strip()] if args.tables else as_is_tables()
    source = HubSource(inventory["repo_id"])
    try:
        outcomes = download_tables(
            inventory, tables, out, source, dry_run=args.dry_run, log=lambda m: print(m, file=sys.stderr)
        )
    except (DownloadError, ManifestError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    total = sum(o.size for o in outcomes)
    for o in outcomes:
        print(f"{o.table:<22}{o.size / 1e6:>10.1f} MB  {o.action}")
    if args.dry_run:
        pending = sum(o.size for o in outcomes if o.action == "planned")
        print(f"dry run: {pending / 1e9:.2f} GB to download of {total / 1e9:.2f} GB in {len(outcomes)} tables; nothing written")
        return 0

    rev_dir = out / inventory["revision"]
    print(f"done: {len(outcomes)} tables verified in {rev_dir}")
    report = pop_closure(rev_dir)
    print(format_report(report))
    if report["checked"]:
        record_check(rev_dir, inventory, "pop_closure", report)
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    loaded = _inventory_and_out(args)
    if loaded is None:
        return 2
    inventory, out = loaded
    try:
        problems = verify_dir(out / inventory["revision"], inventory)
    except ManifestError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    for line in problems:
        print(f"PROBLEM: {line}", file=sys.stderr)
    if problems:
        return 1
    print(f"ok: {out / inventory['revision']} matches its manifest and the inventory")
    return 0


def cmd_closure(args: argparse.Namespace) -> int:
    loaded = _inventory_and_out(args)
    if loaded is None:
        return 2
    inventory, out = loaded
    rev_dir = out / inventory["revision"]
    # Only read files that verify. `download` calls pop_closure straight after verifying; this
    # standalone command would otherwise parse whatever happens to be on disk.
    try:
        problems = verify_dir(rev_dir, inventory)
    except ManifestError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    if problems:
        for line in problems:
            print(f"PROBLEM: {line}", file=sys.stderr)
        print("error: not reading files that fail verification; fix or re-download them first", file=sys.stderr)
        return 1
    report = pop_closure(rev_dir)
    print(format_report(report))
    if report["checked"]:
        record_check(rev_dir, inventory, "pop_closure", report)
    return 0


def _add_location(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("--out", help="parent folder of the raw files (default: data/raw/aidev in the checkout)")
    sub.add_argument("--expected", default=str(DEFAULT_EXPECTED), help="the committed inventory")


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

    dl = sub.add_parser("download", help="download the as_is tables and verify each against the inventory")
    _add_location(dl)
    dl.add_argument("--tables", help="comma-separated subset (default: every as_is table)")
    dl.add_argument("--dry-run", action="store_true", help="show what would be downloaded; write nothing")
    dl.set_defaults(func=cmd_download)

    ver = sub.add_parser("verify", help="re-check a raw folder against its manifest and the inventory (offline)")
    _add_location(ver)
    ver.set_defaults(func=cmd_verify)

    clo = sub.add_parser("closure", help="report pop-scoped rows whose pr_id is not in pull_request")
    _add_location(clo)
    clo.set_defaults(func=cmd_closure)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
