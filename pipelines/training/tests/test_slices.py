"""slices.py: label balance per slice."""
from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import slices  # noqa: E402


def _df():
    rows = []
    for i in range(10):          # MEMBER: 10 rows, all merged, 8 observed, 2 bad
        rows.append({"repo": "o/a", "author_association": "MEMBER", "label_merged": True, "label_bad": i < 2,
                     "label_bad_observed": i < 8, "LA": i, "LD": 0})
    for i in range(4):           # NONE: unmerged, nothing observed, label_bad null
        rows.append({"repo": "o/a", "author_association": "NONE", "label_merged": False, "label_bad": None,
                     "label_bad_observed": None, "LA": 100 + i, "LD": 0})
    return pd.DataFrame(rows)


def _row(t, name, value):
    return t[(t["slice"] == name) & (t["value"] == value)].iloc[0]


def test_rates_use_observed_rows_only():
    t, _ = slices.slice_table(_df(), ("author_association",), min_rows=1, min_observed=1)
    m = _row(t, "author_association", "MEMBER")
    assert (m.rows, m.bad_observed) == (10, 8) and m.bad_rate == 2 / 8 and m.merged_rate == 1.0
    assert m.coverage == 0.8 and m["flags"] == ""


def test_unobserved_is_null_not_zero():
    t, _ = slices.slice_table(_df(), ("author_association",), min_rows=1, min_observed=1)
    n = _row(t, "author_association", "NONE")
    assert n.bad_observed == 0 and pd.isna(n.bad_rate) and n.merged_rate == 0.0 and n["flags"] == "thin_labels"


def test_small_and_thin_flags():
    t, _ = slices.slice_table(_df(), ("author_association",), min_rows=5, min_observed=5)
    assert _row(t, "author_association", "NONE")["flags"] == "small,thin_labels"
    assert _row(t, "author_association", "MEMBER")["flags"] == ""


def test_missing_column_is_skipped_and_empty_table_is_safe():
    t, skipped = slices.slice_table(_df(), ("author_association", "domain"))
    assert skipped == ["domain"] and set(t["slice"]) == {"author_association"}
    t, skipped = slices.slice_table(pd.DataFrame(), ("repo",))
    assert t.empty and skipped == ["repo"] and list(t.columns) == list(slices.COLUMNS)


def test_null_value_is_its_own_slice_and_size_quartile_is_derived():
    df = _df().assign(author_association=lambda d: d.author_association.where(d.label_merged, None))
    t, _ = slices.slice_table(df, ("author_association", "size_quartile"), min_rows=1, min_observed=1)
    assert _row(t, "author_association", "(null)").rows == 4
    assert set(t[t["slice"] == "size_quartile"]["value"]) <= {"Q1", "Q2", "Q3", "Q4"}


def test_all_equal_sizes_do_not_crash():
    df = _df().assign(LA=5, LD=5)
    t, skipped = slices.slice_table(df, ("size_quartile",))
    assert t.empty and skipped == ["size_quartile"]


def test_main_strict_exit_code(tmp_path, capsys):
    p = tmp_path / "d.jsonl"
    _df().to_json(p, orient="records", lines=True)
    assert slices.main([str(p), "--slices", "author_association"]) == 0
    assert slices.main([str(p), "--slices", "author_association", "--strict"]) == 1
    assert slices.main([str(tmp_path / "missing.jsonl")]) == 2
