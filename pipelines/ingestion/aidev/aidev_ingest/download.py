"""Download the `as_is` tables and prove each one is the file the inventory describes.

Per table: download into `.partial/`, verify, then move into place. A table is
therefore either complete and verified, or absent. Nothing here ever overwrites a
final file; one that fails verification stops the run.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from . import manifest
from .pin import TABLES
from .verify import expected_tables, verify_file

# Rename is on the same disk, so the data is never held twice; the margin covers the
# file being written and ordinary filesystem overhead.
FULL_HASH = re.compile(r"[0-9a-f]{40}")
DISK_MARGIN = 1.2
EXTRA_FREE_BYTES = 1_000_000_000
PARTIAL = ".partial"


class DownloadError(Exception):
    pass


class VerificationError(DownloadError):
    pass


class ExistingFileMismatch(DownloadError):
    pass


class DiskSpaceError(DownloadError):
    pass


class UnsafeOutputDir(DownloadError):
    pass


class Fetcher(Protocol):
    def download_file(self, path: str, revision: str, dest_dir: Path) -> Path: ...


@dataclass(frozen=True)
class Outcome:
    table: str
    action: str  # downloaded | skipped | planned | present
    size: int


def ensure_not_committable(out_dir: Path) -> None:
    """Refuse to write raw data where `git add .` could pick it up.

    Outside a git repository there is nothing to check. Inside one, the output
    folder must be ignored. A file inside it is tested, because a rule such as
    `/data/` matches directories and git decides by the path.
    """
    out_dir = out_dir.resolve()  # git resolves a relative path against the repo top, not the cwd
    probe = out_dir
    while not probe.exists():
        probe = probe.parent
    try:
        top = subprocess.run(
            ["git", "-C", str(probe), "rev-parse", "--show-toplevel"], capture_output=True, text=True
        )
    except FileNotFoundError:
        return
    if top.returncode != 0:
        return
    ignored = subprocess.run(
        ["git", "-C", top.stdout.strip(), "check-ignore", "-q", "--", str(out_dir / "x.parquet")],
        capture_output=True,
    )
    if ignored.returncode != 0:
        raise UnsafeOutputDir(
            f"{out_dir} is inside a git repository and is not ignored, so these files could be "
            "committed. Pick a folder the repository ignores (the default is data/raw/aidev)."
        )


def _place(got: Path, final: Path) -> None:
    """Move a verified file into place without ever replacing an existing one.

    os.link fails if `final` exists, so this holds even if something creates it after the
    earlier existence check; os.replace would overwrite it silently. On a filesystem with no
    hard links the fallback keeps the check but accepts that tiny race.
    """
    clash = f"{final} appeared while it was being downloaded; not overwriting raw data"
    try:
        os.link(got, final)
    except FileExistsError:
        raise ExistingFileMismatch(clash) from None
    except OSError:
        if final.exists():
            raise ExistingFileMismatch(clash) from None
        os.replace(got, final)
        return
    got.unlink()


def check_disk(directory: Path, needed_bytes: int) -> None:
    probe = directory
    while not probe.exists():
        probe = probe.parent
    free = shutil.disk_usage(probe).free
    required = int(needed_bytes * DISK_MARGIN) + EXTRA_FREE_BYTES
    if free < required:
        raise DiskSpaceError(
            f"need about {required / 1e9:.1f} GB free at {probe}, only {free / 1e9:.1f} GB is available"
        )


def download_tables(
    inventory: dict[str, Any],
    tables: list[str],
    out_dir: Path,
    fetcher: Fetcher,
    *,
    dry_run: bool = False,
    log: Callable[[str], None] = lambda message: None,
) -> list[Outcome]:
    revision = inventory["revision"]
    if not FULL_HASH.fullmatch(revision):
        raise DownloadError(f"the inventory revision {revision!r} is not a 40-character commit hash; refusing")
    expected = expected_tables(inventory)

    unknown = [t for t in tables if t not in expected]
    if unknown:
        raise DownloadError(f"not in the inventory: {', '.join(unknown)}")
    # The inventory is a committed file, so do not trust it with where a file is written. The
    # table name and path become filesystem paths below; they must be exactly <table>.parquet.
    for t in tables:
        if expected[t].path != f"{t}.parquet" or not re.fullmatch(r"[A-Za-z0-9_]+", t):
            raise DownloadError(f"{t}: the inventory path {expected[t].path!r} is not '{t}.parquet'; refusing")
    wrong_policy = [t for t in tables if TABLES[t].fetch != "as_is"]
    if wrong_policy:
        raise DownloadError(
            f"{', '.join(wrong_policy)} {'is' if len(wrong_policy) == 1 else 'are'} not `as_is` "
            "tables; this command only copies tables exactly as published"
        )

    out_dir = out_dir.resolve()
    rev_dir = out_dir / revision
    order = sorted(tables, key=lambda t: expected[t].size)  # smallest first: a bug shows on a small file
    pending = [t for t in order if not (rev_dir / f"{t}.parquet").exists()]

    if dry_run:
        return [
            Outcome(t, "planned" if t in pending else "present", expected[t].size) for t in order
        ]

    for path in (rev_dir, rev_dir / PARTIAL, *(rev_dir / f"{t}.parquet" for t in tables)):
        if path.is_symlink():
            raise DownloadError(f"{path} is a symbolic link; refusing to write through it")

    ensure_not_committable(out_dir)
    check_disk(rev_dir, sum(expected[t].size for t in pending))
    rev_dir.mkdir(parents=True, exist_ok=True)
    partial = rev_dir / PARTIAL

    outcomes: list[Outcome] = []
    for table in order:
        want = expected[table]
        final = rev_dir / f"{table}.parquet"

        if final.exists():
            problems = verify_file(final, want)
            if problems:
                raise ExistingFileMismatch(
                    f"{table}: {final} already exists but fails verification ({'; '.join(problems)}). "
                    "Not overwriting raw data; move the file aside to download it again."
                )
            manifest.record_table(rev_dir, inventory, want)
            log(f"{table}: already present and verified, skipped")
            outcomes.append(Outcome(table, "skipped", want.size))
            continue

        partial.mkdir(exist_ok=True)
        log(f"{table}: downloading {want.size / 1e6:.1f} MB")
        got = fetcher.download_file(want.path, revision, partial)
        problems = verify_file(got, want)
        if problems:
            got.unlink(missing_ok=True)
            raise VerificationError(
                f"{table}: the downloaded file failed verification ({'; '.join(problems)}). "
                "It was discarded; run the command again."
            )
        _place(got, final)
        manifest.record_table(rev_dir, inventory, want)
        log(f"{table}: verified")
        outcomes.append(Outcome(table, "downloaded", want.size))

    shutil.rmtree(partial, ignore_errors=True)
    return outcomes
