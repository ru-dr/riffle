"""outcome_features.py: no feature sees the future, stale-open rows lend no outcome, the lists match the contract."""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import frames  # noqa: E402
import outcome_features as OF  # noqa: E402

HISTORY = [c for c in OF.ENGINEERED if c.startswith(("ah_", "rh"))]


def _toy(n=3000, seed=0):
    rng = np.random.default_rng(seed)
    created = pd.Timestamp("2024-01-01", tz="UTC") + pd.to_timedelta(rng.uniform(0, 400, n), unit="D")
    closed = created + pd.to_timedelta(rng.exponential(10, n), unit="D")
    return pd.DataFrame({
        "repo": rng.choice(["a/x", "b/y", "c/z"], n), "pr_number": np.arange(n),
        "author_email": rng.choice([f"u{i}@e.com" for i in range(60)] + [None], n),
        "created_at": created, "closed_at": closed, "observed_through": closed.max(),
        "outcome_3class": rng.choice(OF.CLASSES, n), "author_is_bot": False,
        "author_confirmed_maintainer": rng.random(n) < 0.1, "head_from_fork": rng.choice([True, False, None], n),
        "EXP": rng.integers(0, 50, n), "SEXP": rng.integers(0, 10, n), "LA": rng.integers(0, 500, n),
        "LD": rng.integers(0, 100, n), "NF": rng.integers(1, 20, n), "ND": rng.integers(1, 5, n),
        "semantic_risk_1to5": rng.choice([1, 3, None], n), "change_intent": "fix",
        "open_snapshot": rng.choice(["exact", "recovered", "final"], n, p=[0.7, 0.2, 0.1])})


def _same(a, b, cols):
    return [c for c in cols if not np.allclose(a[c].to_numpy(float), b[c].to_numpy(float), equal_nan=True)]


def test_cut():
    """Features of PRs opened before a cut do not change when everything after it is unknown: later PRs removed,
    PRs that closed later still open, with their outcome scrambled."""
    raw = _toy()
    cut = pd.Timestamp("2024-08-01", tz="UTC")
    early = raw[raw["created_at"] < cut].copy()
    late = early["closed_at"] >= cut
    early.loc[late, "closed_at"] = pd.Timestamp("2099-01-01", tz="UTC")
    early.loc[late, "outcome_3class"] = np.random.default_rng(1).choice(OF.CLASSES, int(late.sum()))
    full = OF.build_features(raw).set_index("pr_number")
    part = OF.build_features(early).set_index("pr_number")
    bad = _same(full.loc[part.index], part, OF.ENGINEERED)
    assert not bad, f"features that see the future: {bad}"


def test_stale_open_outcome_never_used():
    """A stale-open row (no closed_at: still open when mined, labelled at mining) lends its outcome to no one."""
    raw = _toy()
    stale = np.random.default_rng(2).random(len(raw)) < 0.08
    raw.loc[stale, "closed_at"] = pd.NaT
    raw.loc[stale, "outcome_3class"] = "goes_nowhere"
    other = raw.copy()
    other.loc[stale, "outcome_3class"] = np.random.default_rng(3).choice(OF.CLASSES, int(stale.sum()))
    a, b = OF.build_features(raw), OF.build_features(other)
    bad = _same(a, b, HISTORY)
    assert not bad, f"features that read a stale-open row's outcome: {bad}"


def test_counting():
    s = OF.prior_sum(np.array([0, 0, 0, 1, -1]), np.array([10, 20, 30, 20, 20]), np.array([15, 40, 35, 5, 1]),
                     np.ones(5))[:, 0]
    assert s[0] == 0 and s[1] == 1 and s[2] == 1 and s[3] == 1 and np.isnan(s[4])
    w = OF.prior_sum(np.zeros(3, int), np.array([100, 100, 100]), np.array([10, 50, 99]), np.ones(3), window=60)
    assert list(w[:, 0]) == [2, 2, 2]                    # 50 and 99 fall inside the 60 s window, 10 does not
    assert np.isnan(OF.prev_event_gap(np.array([0]), np.array([5]), np.array([5]))[0])


def test_seconds_any_unit():
    for unit in ("ns", "us", "ms", "s"):
        assert OF.secs(pd.Series(pd.to_datetime(["1970-01-02T00:00:00Z"]).as_unit(unit)))[0] == 86400, unit


def test_unknown_author_has_no_history():
    d = OF.build_features(_toy())
    unknown = d["author_key"].isna()
    assert unknown.any() and d.loc[unknown, [c for c in OF.ENGINEERED if c.startswith("ah_")]].isna().all().all()


def test_lists_match_the_contract():
    assert len(OF.FEATURES) == len(set(OF.FEATURES)) == 161
    assert not set(OF.FEATURES) & set(OF.LEAKS)
    assert set(OF.CATEGORICAL) <= set(OF.MINER_FEATURES)
    schema = set(frames._schema_props("mined_dataset.schema.json"))
    assert len(schema) > 100, "contracts/mined_dataset.schema.json not found"
    assert not set(OF.MINER_FEATURES) - schema, "miner columns the contract no longer has"
    assert not set(OF.LEAKS) - schema, "leak columns the contract no longer has"
    assert not set(OF.MINER_FEATURES) & frames.non_input_columns(), "contract says these are not inputs"
    assert not set(OF.ENGINEERED) & schema, "an engineered name collides with a miner column"


def test_training_rows_keep_each_repos_class_mix():
    d = OF.build_features(_toy())
    r = OF.training_rows(d)
    assert not (r.train & ~r.scored).any()
    assert not r.train[(d["open_snapshot"] != "exact").to_numpy()].any()
    g = pd.DataFrame({"repo": d["repo"], "y": d["y"], "s": r.scored, "w": r.weight * r.train})
    t = g.groupby(["repo", "y"]).agg(scored=("s", "sum"), weighted=("w", "sum"))
    assert np.allclose(t["scored"], t["weighted"])        # reweighted exact rows stand in for every scored row
    w = OF.repo_weights(d["repo"])
    assert np.isclose(w.mean(), 1.0) and w.min() > 0
