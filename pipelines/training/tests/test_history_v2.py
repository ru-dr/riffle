"""prepare_training_data.history_v2: the ah_*/rh* history features see nothing after a PR opened.

Ported from the point-in-time tests in Ananth's pipeline (ananth_pipeline/pipeline.ipynb, section 3).
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import prepare_training_data as ptd  # noqa: E402

CLASSES = ("lands_clean", "lands_after_rework", "goes_nowhere")


def _toy(n=3000, seed=0):
    rng = np.random.default_rng(seed)
    created = pd.Timestamp("2024-01-01", tz="UTC") + pd.to_timedelta(rng.uniform(0, 400, n), unit="D")
    closed = created + pd.to_timedelta(rng.exponential(10, n), unit="D")
    return pd.DataFrame({
        "repo": rng.choice(["a/x", "b/y", "c/z"], n), "pr_number": np.arange(n),
        "author_email": rng.choice([f"u{i}@e.com" for i in range(60)] + [None], n),
        "created_at": created, "closed_at": closed, "outcome_3class": rng.choice(CLASSES, n),
        "author_is_bot": rng.random(n) < 0.05, "author_confirmed_maintainer": rng.random(n) < 0.1})


def _same(a, b):
    return [c for c in ptd.HISTORY_V2
            if not np.allclose(a[c].to_numpy(float), b[c].to_numpy(float), equal_nan=True)]


def test_features_of_earlier_prs_do_not_change_when_the_future_is_hidden():
    toy = _toy()
    cut = pd.Timestamp("2024-08-01", tz="UTC")
    early = toy[toy["created_at"] < cut].copy()
    late = early["closed_at"] >= cut                       # still open at the cut: outcome unknown then
    early.loc[late, "closed_at"] = pd.NaT
    early.loc[late, "outcome_3class"] = np.random.default_rng(1).choice(CLASSES, int(late.sum()))
    full = pd.concat([toy, ptd.history_v2(toy)], axis=1).set_index("pr_number")
    part = pd.concat([early, ptd.history_v2(early)], axis=1).set_index("pr_number")
    assert not _same(full.loc[part.index], part), "features that see the future"


def test_prs_without_a_close_time_lend_no_outcome():
    toy = _toy()
    stale = np.random.default_rng(2).random(len(toy)) < 0.08
    a, b = toy.copy(), toy.copy()
    a.loc[stale, "closed_at"] = b.loc[stale, "closed_at"] = pd.NaT
    a.loc[stale, "outcome_3class"] = "goes_nowhere"
    b.loc[stale, "outcome_3class"] = np.random.default_rng(3).choice(CLASSES, int(stale.sum()))
    assert not _same(ptd.history_v2(a), ptd.history_v2(b)), "features that read a stale-open PR's outcome"


def test_unknown_authors_get_blank_author_history():
    toy = _toy()
    h = ptd.history_v2(toy)
    unknown = toy["author_email"].isna().to_numpy()
    assert unknown.any()
    for c in ptd.HISTORY_V2:
        if c.startswith("ah_"):
            assert h.loc[unknown, c].isna().all(), c


def test_prior_sum_counts_strictly_earlier_events_of_the_same_key():
    s = ptd.prior_sum(np.array([0, 0, 0, 1, -1]), np.array([10, 20, 30, 20, 20]), np.array([15, 40, 35, 5, 1]),
                      np.ones(5))[:, 0]
    assert s[0] == 0 and s[1] == 1 and s[2] == 1 and s[3] == 1 and np.isnan(s[4])


def test_seconds_do_not_depend_on_the_timestamp_unit():
    for unit in ("ns", "us", "ms", "s"):
        t = pd.Series(pd.to_datetime(["1970-01-02T00:00:00Z"]).as_unit(unit))
        assert ptd.secs(t)[0] == 86400
    assert np.isnan(ptd.secs(pd.Series([None]))[0])


def test_prs_still_open_count_as_open_now():
    """In production every open PR has no closed_at, so a PR without one must count as open (not be left out)."""
    d = pd.DataFrame({
        "repo": ["o/r"] * 3, "pr_number": [1, 2, 3], "author_email": ["a@x", "a@x", "b@x"],
        "created_at": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-05"], utc=True),
        "closed_at": pd.to_datetime([None, "2024-01-10", None], utc=True),
        "outcome_3class": ["goes_nowhere", "lands_clean", "goes_nowhere"],
        "author_is_bot": False, "author_confirmed_maintainer": False})
    h = ptd.history_v2(d)
    assert h.loc[2, "rh_open_now"] == 2          # PR 1 (no close time) and PR 2 (closes later) are open on Jan 5
    assert h.loc[1, "ah_open_now"] == 1          # the author's PR 1 is still open when PR 2 opens
    assert h.loc[2, "rh90_n"] == 0               # but neither lends an outcome yet
