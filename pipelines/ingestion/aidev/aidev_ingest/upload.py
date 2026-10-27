"""Upload verified local tables to the raw bucket, verify them there, then delete the local copy.

For every table, in this order and for these reasons:

1. Verify the local file again. Bytes that do not match the inventory never go up.
2. Look in the bucket. An object that is already there is accepted only if it carries the
   expected checksum; it is never replaced.
3. Create the object with a "must not already exist" precondition, so Google, not just this
   code, refuses an overwrite.
4. Compare the object's md5 as the bucket reports it with the md5 of the local file.
5. Only then delete the local file.

The bucket cannot take an object back (retention, and the job account has no delete role), so
nothing may reach it unverified. Each step is safe to repeat: a run that dies between any two
steps is finished by the next one.

The bucket itself is a parameter, never a constant: its name stays out of the repository.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from . import manifest
from .download import FULL_HASH
from .pin import TABLES
from .verify import CHUNK, expected_tables, verify_file

PREFIX = "raw/aidev"
MANIFESTS = "_manifests"


class UploadError(Exception):
    pass


class RemoteMismatch(UploadError):
    pass


class ObjectExists(Exception):
    """Raised by a bucket when the object to create already exists."""


@dataclass(frozen=True)
class RemoteObject:
    name: str
    size: int
    md5_b64: str
    metadata: dict[str, str]


class Bucket(Protocol):
    """What the upload needs from a bucket. GcsBucket is the real one; tests use a fake."""

    def get(self, name: str) -> RemoteObject | None: ...
    def create(self, name: str, path: Path, metadata: dict[str, str]) -> RemoteObject: ...
    def create_text(self, name: str, text: str) -> None: ...
    def list(self, prefix: str) -> list[RemoteObject]: ...


@dataclass(frozen=True)
class UploadOutcome:
    table: str
    action: str  # uploaded | present | planned | held
    size: int
    local_deleted: bool = False


def md5_b64(path: Path) -> str:
    """md5 in the base64 form GCS reports. Used for transfer integrity, not security."""
    digest = hashlib.md5(usedforsecurity=False)
    with open(path, "rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return base64.b64encode(digest.digest()).decode("ascii")


def object_name(revision: str, table: str) -> str:
    return f"{PREFIX}/{revision}/{table}.parquet"


def _metadata(want: Any, revision: str) -> dict[str, str]:
    return {"sha256": want.sha256, "source_revision": revision, "table": want.table}


def _check_remote(remote: RemoteObject, want: Any, local_md5: str | None, table: str) -> None:
    """Accept a bucket object only if it is the file the inventory describes."""
    problems = []
    if remote.size != want.size:
        problems.append(f"size {remote.size}, expected {want.size}")
    if remote.metadata.get("sha256") != want.sha256:
        problems.append("it does not carry the expected sha256")
    if local_md5 is not None and remote.md5_b64 != local_md5:
        problems.append("its md5 differs from the local file's")
    if problems:
        raise RemoteMismatch(
            f"{table}: the bucket's {remote.name} is not the verified file ({'; '.join(problems)}). "
            "Not overwriting. Any local copy is kept. This needs a person to look at it."
        )


def upload_tables(
    inventory: dict[str, Any],
    tables: list[str],
    out_dir: Path,
    bucket: Bucket,
    *,
    dry_run: bool = False,
    keep_local: bool = False,
    allow_held: tuple[str, ...] = (),
    log: Callable[[str], None] = lambda message: None,
) -> list[UploadOutcome]:
    revision = inventory["revision"]
    if not FULL_HASH.fullmatch(revision):
        raise UploadError(f"the inventory revision {revision!r} is not a 40-character commit hash; refusing")
    expected = expected_tables(inventory)

    unknown = [t for t in tables if t not in expected]
    if unknown:
        raise UploadError(f"not in the inventory: {', '.join(unknown)}")
    for t in tables:
        if expected[t].path != f"{t}.parquet" or TABLES[t].fetch != "as_is":
            raise UploadError(f"{t}: only as_is tables whose inventory path is '{t}.parquet' can be uploaded")
    stray = [t for t in allow_held if t not in tables or not TABLES[t].hold_upload]
    if stray:
        raise UploadError(f"--allow-held names tables that are not held or not selected: {', '.join(stray)}")

    rev_dir = out_dir.resolve() / revision
    starting = manifest.load(rev_dir)
    if starting is None:
        raise UploadError(f"{rev_dir} has no manifest; run `download` first")
    owed = bool(starting.get("snapshot_pending", False))

    def owe_snapshot() -> None:
        """Record, before the first change takes effect, that a snapshot of the manifest is owed.

        Done up front so that a run which dies after changing something still leaves the flag
        behind, and the next run writes the snapshot even if it has nothing else to do.
        """
        nonlocal owed
        if not owed and not dry_run:
            manifest.set_flag(rev_dir, inventory, "snapshot_pending", True)
            owed = True

    outcomes: list[UploadOutcome] = []
    for table in sorted(tables, key=lambda t: expected[t].size):
        want = expected[table]
        hold = TABLES[table].hold_upload
        if hold and table not in allow_held:
            log(f"{table}: HELD, not uploaded: {hold}")
            outcomes.append(UploadOutcome(table, "held", want.size))
            continue

        entry = manifest.load(rev_dir)["tables"].get(table)
        if entry is None:
            raise UploadError(f"{table}: not in the manifest; download and verify it first")
        local = rev_dir / f"{table}.parquet"
        if local.is_symlink():
            raise UploadError(f"{local} is a symbolic link; refusing to read through it")

        has_local = local.exists()
        if has_local:
            problems = verify_file(local, want)
            if problems:
                raise UploadError(f"{table}: the local file fails verification ({'; '.join(problems)}); not uploading it")
            local_md5: str | None = md5_b64(local)
        elif entry["state"] in ("uploaded", "local_deleted"):
            # `uploaded` with no file means a run died between deleting the file and recording
            # that; the bucket is checked below, so this is recoverable, not a mystery.
            local_md5 = entry.get("remote_md5")  # what the bucket reported when it was uploaded
        else:
            raise UploadError(f"{table}: the local file is missing and the manifest does not say it was uploaded")

        name = object_name(revision, table)
        remote = bucket.get(name)
        if remote is not None:
            _check_remote(remote, want, local_md5, table)
            action = "present"
        elif not has_local:
            raise UploadError(
                f"{table}: the manifest says it was uploaded and removed locally, but the bucket has no "
                f"{name}. Nothing here can recreate it; this needs a person."
            )
        elif dry_run:
            log(f"{table}: would upload {want.size / 1e6:.1f} MB")
            outcomes.append(UploadOutcome(table, "planned", want.size))
            continue
        else:
            log(f"{table}: uploading {want.size / 1e6:.1f} MB")
            owe_snapshot()
            try:
                remote = bucket.create(name, local, _metadata(want, revision))
            except ObjectExists:
                # Lost a race: someone created it between the check and the create.
                remote = bucket.get(name)
                if remote is None:
                    raise UploadError(f"{table}: {name} reported as existing but cannot be read") from None
            _check_remote(remote, want, local_md5, table)
            action = "uploaded"

        deleted = False
        if dry_run:
            outcomes.append(UploadOutcome(table, action, want.size))
            continue
        if has_local and not keep_local:
            owe_snapshot()
            manifest.record_table(rev_dir, inventory, want, state="uploaded", remote_md5=local_md5)
            local.unlink()
            manifest.record_table(rev_dir, inventory, want, state="local_deleted", remote_md5=local_md5)
            deleted = True
            log(f"{table}: verified in the bucket, local copy deleted")
        elif has_local:
            owe_snapshot()
            manifest.record_table(rev_dir, inventory, want, state="uploaded", remote_md5=local_md5)
            log(f"{table}: verified in the bucket, local copy kept")
        else:
            if entry["state"] == "uploaded":  # finish what a crashed run left half recorded
                owe_snapshot()
                manifest.record_table(rev_dir, inventory, want, state="local_deleted", remote_md5=local_md5)
            how = (
                "size, checksum and md5"
                if local_md5
                else "size and recorded checksum only, because no md5 was recorded for it"
            )
            log(f"{table}: already in the bucket; verified by {how}")
        outcomes.append(UploadOutcome(table, action, want.size, deleted))

    if owed and not dry_run:
        _snapshot_manifest(bucket, rev_dir, revision)
        manifest.set_flag(rev_dir, inventory, "snapshot_pending", False)
    return outcomes


def _snapshot_manifest(bucket: Bucket, rev_dir: Path, revision: str) -> None:
    """Store a copy of the manifest in the bucket under a unique name.

    One new object per run, never a replacement: an object already there is never touched, the
    same rule as the data. The newest name is the current state, except while a snapshot is
    owed (a run died before writing it); `verify_remote` reports that.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    name = f"{PREFIX}/{revision}/{MANIFESTS}/{stamp}.json"
    snapshot = {**manifest.load(rev_dir), "snapshot_pending": False}  # the stored copy is the finished state
    try:
        bucket.create_text(name, json.dumps(snapshot, indent=2) + "\n")
    except ObjectExists:
        raise UploadError(
            "a manifest snapshot with this timestamp already exists; run the same command again"
        ) from None


def verify_remote(inventory: dict[str, Any], out_dir: Path, bucket: Bucket) -> list[str]:
    """Check the bucket against the manifest, without downloading anything."""
    revision = inventory["revision"]
    if not FULL_HASH.fullmatch(revision):
        return [f"the inventory revision {revision!r} is not a 40-character commit hash"]
    rev_dir = out_dir.resolve() / revision
    loaded = manifest.load(rev_dir)
    if loaded is None:
        return [f"{rev_dir} has no manifest"]
    expected = expected_tables(inventory)

    problems: list[str] = []
    if loaded.get("snapshot_pending"):
        problems.append(
            "a manifest snapshot is owed: the newest one in the bucket is behind the local manifest; "
            "run `upload` to write it"
        )
    uploaded = {
        t: e for t, e in loaded["tables"].items() if e["state"] in ("uploaded", "local_deleted")
    }
    for table, entry in sorted(uploaded.items()):
        if table not in expected:
            problems.append(f"{table}: in the manifest but not in the inventory")
            continue
        remote = bucket.get(object_name(revision, table))
        if remote is None:
            problems.append(f"{table}: the manifest says uploaded but the bucket has no such object")
            continue
        want = expected[table]
        if remote.size != want.size:
            problems.append(f"{table}: bucket size {remote.size}, expected {want.size}")
        if remote.metadata.get("sha256") != want.sha256:
            problems.append(f"{table}: the bucket object does not carry the expected sha256")
        recorded = entry.get("remote_md5")
        if recorded is not None and remote.md5_b64 != recorded:
            problems.append(f"{table}: the bucket's md5 differs from the one recorded at upload")

    prefix = f"{PREFIX}/{revision}/"
    known = {object_name(revision, t) for t in expected}
    snapshots = 0
    for remote in bucket.list(prefix):
        if remote.name.startswith(f"{prefix}{MANIFESTS}/"):
            snapshots += 1
        elif remote.name not in known:
            problems.append(f"{remote.name}: in the bucket but not a table in the inventory")
    if uploaded and not snapshots:
        problems.append("tables were uploaded but the bucket holds no manifest snapshot")
    return problems
