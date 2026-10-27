"""The manifest: what is in a raw folder, and that it was verified.

Written last and atomically, so its presence for a table means that table was
complete and verified. Paths are relative and nothing here names a person or a
machine, so the file is safe to keep next to the data and to share.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .pin import TABLES
from .verify import Expected, expected_tables, verify_file

MANIFEST = "manifest.json"
SCHEMA_VERSION = 1


class ManifestError(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def inventory_digest(inventory: dict[str, Any]) -> str:
    """Identity of the inventory a folder was checked against. Ignores `meta`."""
    body = {key: value for key, value in inventory.items() if key != "meta"}
    text = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load(rev_dir: Path) -> dict[str, Any] | None:
    path = rev_dir / MANIFEST
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:  # ValueError covers bad JSON and bad UTF-8
        raise ManifestError(f"{path} is damaged ({type(error).__name__}); it cannot be read") from None
    missing = [key for key in ("revision", "inventory_sha256", "tables") if not isinstance(data, dict) or key not in data]
    if missing:
        raise ManifestError(f"{path} is damaged: missing {', '.join(missing)}")
    return data


def write(rev_dir: Path, manifest: dict[str, Any]) -> None:
    """Write via a uniquely named temporary file so a reader never sees half a manifest.

    mkstemp creates the file exclusively, so a pre-planted file or link at a guessable name is
    never written through.
    """
    descriptor, name = tempfile.mkstemp(prefix=".manifest-", suffix=".tmp", dir=rev_dir)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(manifest, indent=2) + "\n")
        os.replace(name, rev_dir / MANIFEST)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise


def _new(inventory: dict[str, Any]) -> dict[str, Any]:
    now = _now()
    return {
        "schema_version": SCHEMA_VERSION,
        "repo_id": inventory["repo_id"],
        "revision": inventory["revision"],
        "inventory_sha256": inventory_digest(inventory),
        "tool_version": __version__,
        "created_at": now,
        "updated_at": now,
        "tables": {},
        "checks": {},
    }


def _open(rev_dir: Path, inventory: dict[str, Any]) -> dict[str, Any]:
    manifest = load(rev_dir)
    if manifest is None:
        return _new(inventory)
    if manifest["inventory_sha256"] != inventory_digest(inventory):
        raise ManifestError(
            f"{rev_dir / MANIFEST} was written against a different inventory; "
            "move this folder aside rather than mixing two pins."
        )
    return manifest


def record_table(
    rev_dir: Path,
    inventory: dict[str, Any],
    expected: Expected,
    state: str = "local_verified",
    **extra: Any,
) -> None:
    """Record one table. `extra` carries facts learned later, such as the bucket's md5."""
    manifest = _open(rev_dir, inventory)
    plan = TABLES[expected.table]
    previous = manifest["tables"].get(expected.table, {})
    manifest["tables"][expected.table] = {
        **{k: v for k, v in previous.items() if k in ("remote_md5",)},
        **extra,
        "file": f"{expected.table}.parquet",
        "size": expected.size,
        "sha256": expected.sha256,
        "rows": expected.rows,
        "columns": [{"name": n, "type": t} for n, t in expected.columns],
        "fetch": plan.fetch,
        "scope": plan.scope,
        "state": state,
    }
    manifest["updated_at"] = _now()
    manifest["tool_version"] = __version__
    write(rev_dir, manifest)


def set_flag(rev_dir: Path, inventory: dict[str, Any], name: str, value: bool) -> None:
    """A top-level yes/no fact about the folder, such as whether a snapshot is still owed."""
    manifest = _open(rev_dir, inventory)
    manifest[name] = value
    manifest["updated_at"] = _now()
    write(rev_dir, manifest)


def record_check(rev_dir: Path, inventory: dict[str, Any], name: str, result: dict[str, Any]) -> None:
    manifest = _open(rev_dir, inventory)
    manifest["checks"][name] = {**result, "checked_at": _now()}
    manifest["updated_at"] = _now()
    write(rev_dir, manifest)


def verify_dir(rev_dir: Path, inventory: dict[str, Any]) -> list[str]:
    """Re-check a raw folder against its manifest and the inventory. No network.

    A table whose state is `local_deleted` (uploaded, local copy removed) is not
    expected on disk.
    """
    manifest = load(rev_dir)
    if manifest is None:
        return [f"{rev_dir / MANIFEST} is missing"]
    problems: list[str] = []
    if manifest["revision"] != inventory["revision"]:
        problems.append(f"manifest is for revision {manifest['revision']}, inventory for {inventory['revision']}")
    if manifest["inventory_sha256"] != inventory_digest(inventory):
        problems.append("manifest was written against a different inventory")

    expected = expected_tables(inventory)
    for name, entry in sorted(manifest["tables"].items()):
        if name not in expected:
            problems.append(f"{name}: in the manifest but not in the inventory")
            continue
        want = expected[name]
        if (entry["size"], entry["sha256"], entry["rows"]) != (want.size, want.sha256, want.rows):
            problems.append(f"{name}: manifest disagrees with the inventory")
        if entry["file"] != f"{name}.parquet":
            problems.append(f"{name}: manifest names the file {entry['file']!r}, expected '{name}.parquet'")
            continue
        if entry["state"] == "local_deleted":
            continue
        for reason in verify_file(rev_dir / entry["file"], want):
            problems.append(f"{name}: {reason}")

    listed = {entry["file"] for entry in manifest["tables"].values()}
    for path in sorted(rev_dir.glob("*.parquet")):
        if path.name not in listed:
            problems.append(f"{path.name}: on disk but not in the manifest")
    return problems
