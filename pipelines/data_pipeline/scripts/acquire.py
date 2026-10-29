"""Stage 1, acquire: download the pinned AIDev tables and verify each one.

Run from pipelines/data_pipeline:  python -m scripts.acquire

It reads the commit and the table list from params.yaml, refuses a commit that is not the
one the committed inventory describes, then downloads each table and checks it (size,
SHA-256, rows, columns) before it is placed. A finished folder is checked once more
against its manifest. All of that goes to the terminal and to logs/acquire.log.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

import yaml
from aidev_ingest.__main__ import DEFAULT_EXPECTED
from aidev_ingest.download import DownloadError, Fetcher, download_tables
from aidev_ingest.hub import HubSource
from aidev_ingest.manifest import ManifestError, verify_dir

from scripts.logging_setup import ROOT, setup_logging

log = logging.getLogger("acquire")

COMMIT = re.compile(r"[0-9a-f]{40}")


class ParamsError(Exception):
    """params.yaml is missing, unreadable or says something this stage cannot act on."""


def load_params(path: Path) -> dict[str, Any]:
    """Return {"commit": str, "tables": list[str]} from the `aidev` section of params.yaml."""
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ParamsError(f"{path} does not exist") from None
    except (OSError, yaml.YAMLError) as error:
        raise ParamsError(f"cannot read {path}: {error}") from None

    section = document.get("aidev") if isinstance(document, dict) else None
    if not isinstance(section, dict):
        raise ParamsError(f"{path} has no 'aidev' section")

    commit = section.get("commit")
    if not isinstance(commit, str) or not COMMIT.fullmatch(commit):
        raise ParamsError("aidev.commit must be a 40-character lowercase commit hash")

    tables = section.get("tables")
    if isinstance(tables, str):
        tables = tables.split(",")
    if not isinstance(tables, list) or not all(isinstance(t, str) for t in tables):
        raise ParamsError("aidev.tables must be a comma-separated string or a list of names")
    tables = [t.strip() for t in tables if t.strip()]
    if not tables:
        raise ParamsError("aidev.tables is empty; name at least one table")
    return {"commit": commit, "tables": tables}


def run(
    params: dict[str, Any],
    out: Path,
    expected: Path,
    source: Fetcher | None = None,
    dry_run: bool = False,
) -> int:
    """Download and verify. Returns a process exit code: 0 ok, 1 failed."""
    inventory = json.loads(expected.read_text(encoding="utf-8"))
    if params["commit"] != inventory.get("revision"):
        log.error(
            "params.yaml pins %s but the committed inventory describes %s; refusing to download",
            params["commit"],
            inventory.get("revision"),
        )
        return 1

    log.info("acquire %s at %s into %s", ",".join(params["tables"]), params["commit"][:12], out)
    fetcher = source or HubSource(inventory["repo_id"])
    outcomes = download_tables(inventory, params["tables"], out, fetcher, dry_run=dry_run, log=log.info)
    for outcome in outcomes:
        log.info("%-22s %10.1f MB  %s", outcome.table, outcome.size / 1e6, outcome.action)
    if dry_run:
        log.info("dry run: nothing written")
        return 0

    problems = verify_dir(out / inventory["revision"], inventory)
    for problem in problems:
        log.error("verify: %s", problem)
    if problems:
        return 1
    log.info("ok: %d tables verified", len(outcomes))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts.acquire", description=__doc__.splitlines()[0])
    parser.add_argument("--params", type=Path, default=ROOT / "params.yaml")
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "raw", help="parent of the <commit> folder")
    parser.add_argument("--expected", type=Path, default=DEFAULT_EXPECTED, help="the committed inventory")
    parser.add_argument("--dry-run", action="store_true", help="show what would be downloaded; write nothing")
    args = parser.parse_args(argv)

    log_path = setup_logging("acquire")
    log.info("logging to %s", log_path)
    try:
        return run(load_params(args.params), args.out, args.expected, dry_run=args.dry_run)
    except (ParamsError, DownloadError, ManifestError, OSError, ValueError) as error:
        log.error("acquire failed: %s", error)
        return 1
    except Exception:  # keep the traceback in the log file, then fail the stage
        log.exception("acquire failed with an unexpected error")
        return 1


if __name__ == "__main__":
    sys.exit(main())
