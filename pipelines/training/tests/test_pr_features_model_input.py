"""A pr_features row (ADR 0027) becomes the model's exact input with the training feature code.

The scorer builds its input the way training did: the miner's row through
outcome_features.build_features, then the 161 FEATURES in order. This runs that on the
fixture message, so a change to either side that breaks the hand-over fails here.

Run: python -m pytest pipelines/training/tests
"""
import json
import os
import pathlib
import sys

import pytest

pd = pytest.importorskip("pandas")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import outcome_features as OF  # noqa: E402

FIXTURE = pathlib.Path(__file__).resolve().parents[3] / "fixtures" / "pr_features" / "pull_request.opened.json"


@pytest.fixture(scope="module")
def row():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["row"]


def test_the_row_holds_every_miner_column_the_model_reads(row):
    missing = [c for c in OF.MINER_FEATURES if c not in row]
    assert missing == []


def test_the_row_carries_no_column_the_model_must_not_read_as_input(row):
    # Leaky columns may be present in the row (the miner writes them) but are never features.
    assert not set(OF.FEATURES) & set(OF.LEAKS)


def test_the_row_becomes_the_161_model_inputs_in_order(row):
    d = OF.build_features(pd.DataFrame([row]))
    X = d[list(OF.FEATURES)]
    assert list(X.columns) == list(OF.FEATURES) and len(OF.FEATURES) == 161
    assert len(X) == 1
    # With no earlier PRs in the table, the history features say "no history", not an error.
    assert X["ah_n"].iloc[0] == 0
    # The LLM was off: the model sees that the way it saw it in training (c_llm_ran = 0).
    assert X["c_llm_ran"].iloc[0] == 0
