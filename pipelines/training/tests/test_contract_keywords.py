"""The mined contracts carry the keywords the training loader reads."""
from __future__ import annotations

import json
import os
import sys

import pytest

pytest.importorskip("pandas")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import frames  # noqa: E402

CONTRACTS = os.path.join(HERE, "..", "..", "..", "contracts")


def _props(name):
    with open(os.path.join(CONTRACTS, name), encoding="utf-8") as fh:
        return json.load(fh)["properties"]


@pytest.mark.parametrize("name", ["mined_dataset.schema.json", "mined_snapshot.schema.json"])
def test_every_diff_feature_is_marked_in_the_contract(name):
    props = _props(name)
    marked = {c for c, v in props.items() if v.get("x-riffle-diff-derived") is True}
    assert marked == set(frames.DIFF_FEATURES)
    assert marked <= frames.diff_derived_columns()


def test_commit_features_mark_the_diff_columns_they_hold():
    props = _props("mined_commit_features.schema.json")
    marked = {c for c, v in props.items() if v.get("x-riffle-diff-derived") is True}
    assert marked == set(frames.DIFF_FEATURES) & set(props)


@pytest.mark.parametrize("name", ["mined_dataset.schema.json", "mined_snapshot.schema.json"])
def test_provenance_and_label_bookkeeping_columns_are_non_inputs(name):
    props = _props(name)
    for c in frames.METADATA_NON_INPUTS + ("szz_window_closed", "llm_diff_truncated"):
        assert props[c].get("x-riffle-non-input") is True, c
    assert set(frames.METADATA_NON_INPUTS) | {"szz_window_closed", "llm_diff_truncated"} <= frames.non_input_columns()
