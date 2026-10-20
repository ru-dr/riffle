"""The miner and its contracts stay in step. Runs in CI with the other miner tests.

Column agreement, row validation and the flat-form round trip live next to the code that
writes each table (test_dataset_contract.py for the dataset; test_snapshots.py for snapshots
and commit features). This file adds the version bookkeeping."""
from __future__ import annotations

import json
import os
import re
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, HERE)

import version  # noqa: E402

MINED = ("mined_dataset", "mined_snapshot", "mined_commit_features")


def _schema(name):
    with open(os.path.join(ROOT, "contracts", f"{name}.schema.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _tuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


@pytest.mark.parametrize("name", MINED)
def test_the_miner_version_a_schema_records_is_not_ahead_of_the_miner(name):
    recorded = _schema(name)["x-riffle-miner-version"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", recorded), recorded
    assert _tuple(recorded) <= _tuple(version.__version__), (
        f"{name} records miner {recorded}, but the miner is {version.__version__}")


@pytest.mark.parametrize("name", MINED)
def test_every_schema_version_has_a_row_in_the_contracts_history(name):
    """CLAUDE.md: after changing a schema, add a row to docs/contracts.md and bump x-riffle-version."""
    ver = _schema(name)["x-riffle-version"]
    with open(os.path.join(ROOT, "docs", "contracts.md"), encoding="utf-8") as fh:
        text = fh.read()
    section = re.search(rf"## `{name}\.schema\.json`(.*?)(?=\n## |\Z)", text, re.S)
    assert section, f"docs/contracts.md has no section for {name}"
    assert re.search(rf"^\| {re.escape(ver)} \|", section.group(1), re.M), (
        f"{name} is at {ver} but docs/contracts.md has no {ver} row")
