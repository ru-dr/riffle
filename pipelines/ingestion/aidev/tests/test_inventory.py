"""Offline tests: a fake hub, plus a real Parquet file written to a temp folder."""

from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from aidev_ingest.__main__ import DEFAULT_EXPECTED
from aidev_ingest.hub import FileInfo, ParquetInfo, summarize_parquet
from aidev_ingest.inventory import build_inventory, check_plan, compare, summary, table_name
from aidev_ingest.pin import REVISION, TABLES, planned_tables

REPO = "example/repo"
REV = "a" * 40


class FakeSource:
    """Stands in for the hub. Files are given in a deliberately unsorted order."""

    def __init__(self, files: list[FileInfo], parquet: dict[str, ParquetInfo]) -> None:
        self._files = files
        self._parquet = parquet

    def resolve(self, ref: str) -> str:
        return REV

    def list_files(self, revision: str):
        return iter(self._files)

    def parquet_info(self, path: str, revision: str) -> ParquetInfo:
        return self._parquet[path]


def _complete_source() -> FakeSource:
    """One parquet file per planned table, each with the columns the plan drops."""
    files, parquet = [FileInfo("README.md", 10, None, "b1")], {}
    for i, name in enumerate(reversed(planned_tables())):
        path = f"{name}.parquet"
        files.append(FileInfo(path, 1000 + i, f"sha{i}", f"blob{i}"))
        columns = [("id", "int64")] + [(c, "string") for c in TABLES[name].drop_columns]
        parquet[path] = ParquetInfo(rows=100 + i, row_groups=2, columns=tuple(columns))
    return FakeSource(files, parquet)


@pytest.fixture
def inventory() -> dict:
    return build_inventory(_complete_source(), REPO, REV)


def test_files_are_sorted_and_only_parquet_has_table_metadata(inventory):
    paths = [f["path"] for f in inventory["files"]]
    assert paths == sorted(paths)
    readme = next(f for f in inventory["files"] if f["path"] == "README.md")
    assert "parquet" not in readme and "table" not in readme
    pull_request = next(f for f in inventory["files"] if f.get("table") == "pull_request")
    assert pull_request["parquet"]["columns"][0] == {"name": "id", "type": "int64"}


def test_table_name():
    assert table_name("pr_timeline.parquet") == "pr_timeline"


def test_identical_inventories_compare_equal(inventory):
    assert compare(inventory, copy.deepcopy(inventory)) == []


def test_meta_is_not_compared(inventory):
    other = {**copy.deepcopy(inventory), "meta": {"generated_at": "2099-01-01T00:00:00Z"}}
    assert compare(inventory, other) == []


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda inv: inv["files"][1].update(lfs_sha256="changed"), "lfs_sha256"),
        (lambda inv: inv["files"][1].update(size=1), "size"),
        (lambda inv: inv["files"][1]["parquet"].update(rows=1), "rows expected"),
        (lambda inv: inv["files"][1]["parquet"]["columns"][0].update(type="double"), "is double"),
        (lambda inv: inv["files"][1]["parquet"]["columns"].pop(0), "missing"),
        (lambda inv: inv["files"].pop(1), "missing"),
        (lambda inv: inv["files"].append({"path": "new.parquet", "size": 1, "lfs_sha256": None, "blob_id": None}), "not in the expected"),
        (lambda inv: inv.update(revision="b" * 40), "revision"),
    ],
)
def test_drift_is_reported(inventory, mutate, expected):
    changed = copy.deepcopy(inventory)
    mutate(changed)
    problems = compare(inventory, changed)
    assert problems, "a change must be reported"
    assert any(expected in p for p in problems), problems


def test_plan_is_satisfied_by_a_complete_revision(inventory):
    assert check_plan(inventory) == []


def test_plan_reports_a_missing_table(inventory):
    inventory["files"] = [f for f in inventory["files"] if f.get("table") != "pr_timeline"]
    assert any("pr_timeline" in p for p in check_plan(inventory))


def test_plan_reports_a_column_to_drop_that_is_gone(inventory):
    for entry in inventory["files"]:
        if entry.get("table") == "pr_commit_details":
            entry["parquet"]["columns"] = [c for c in entry["parquet"]["columns"] if c["name"] != "patch"]
    assert any("patch" in p for p in check_plan(inventory))


def test_summary_lists_every_file(inventory):
    text = summary(inventory)
    for entry in inventory["files"]:
        assert entry["path"] in text


def test_summarize_parquet_reads_a_real_footer(tmp_path: Path):
    table = pa.table({"id": pa.array([1, 2, 3], pa.int64()), "name": ["a", "b", "c"]})
    path = tmp_path / "t.parquet"
    pq.write_table(table, path, row_group_size=2)
    with open(path, "rb") as handle:
        info = summarize_parquet(handle)
    assert info == ParquetInfo(rows=3, row_groups=2, columns=(("id", "int64"), ("name", "string")))


def test_pin_is_a_full_commit_hash():
    assert re.fullmatch(r"[0-9a-f]{40}", REVISION)


def test_every_table_has_a_valid_policy_and_reason():
    for name, plan in TABLES.items():
        assert plan.fetch in {"full", "pop_filtered", "skip"}, name
        assert plan.reason, name
        if plan.fetch == "skip":
            assert name not in planned_tables()


def test_committed_inventory_matches_the_pin_and_the_plan():
    """The committed pin is the contract later stages download against."""
    expected = json.loads(DEFAULT_EXPECTED.read_text(encoding="utf-8"))
    assert expected["revision"] == REVISION
    assert expected["schema_version"] == 1
    assert check_plan(expected) == []
    for entry in expected["files"]:
        if entry["path"].endswith(".parquet"):
            assert entry["lfs_sha256"], f"{entry['path']} has no LFS checksum to verify against"


needs_network = pytest.mark.skipif(
    not os.environ.get("RUN_NETWORK_TESTS"), reason="set RUN_NETWORK_TESTS=1 to talk to the Hub"
)


@pytest.mark.network
@needs_network
def test_hub_still_matches_the_committed_inventory():
    from aidev_ingest.__main__ import main

    assert main(["check"]) == 0


@pytest.mark.network
@needs_network
def test_resolve_returns_the_commit_that_was_asked_for():
    """dataset_info().sha does not; this guards the replacement."""
    from aidev_ingest.hub import HubSource

    hub = HubSource()
    assert hub.resolve(REVISION) == REVISION
    assert hub.resolve("v4") != REVISION
    assert re.fullmatch(r"[0-9a-f]{40}", hub.resolve("main"))
    with pytest.raises(ValueError):
        hub.resolve("no-such-tag")
    with pytest.raises(Exception):  # RevisionNotFoundError
        hub.resolve("0" * 40)
