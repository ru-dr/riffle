"""Cut test for fe_features: features of PRs opened before a cut must not change when everything after the cut is
unknown (later PRs removed, PRs closed after the cut still open with no outcome)."""
import numpy as np
import pandas as pd
import pytest

import fe_features as F


def _seq():
    """fe_seq.py (the sequence model) needs torch, LightGBM, SciPy and scikit-learn; CI's pipelines job has none."""
    for m in ("torch", "lightgbm", "scipy", "sklearn"):
        pytest.importorskip(m)
    import fe_seq
    return fe_seq


def _toy(n=3000, seed=0):
    rng = np.random.default_rng(seed)
    t0 = pd.Timestamp("2024-01-01", tz="UTC")
    created = t0 + pd.to_timedelta(rng.uniform(0, 400, n), unit="D")
    closed = created + pd.to_timedelta(rng.exponential(10, n), unit="D")
    return pd.DataFrame({
        "repo": rng.choice(["a/x", "b/y", "c/z"], n), "pr_number": np.arange(n),
        "author_email": rng.choice([f"u{i}@e.com" for i in range(60)] + [None], n),
        "created_at": created, "closed_at": closed, "observed_through": closed.max(),
        "outcome_3class": rng.choice(F.CLASSES, n), "author_is_bot": False,
        "author_confirmed_maintainer": rng.random(n) < 0.1, "head_from_fork": rng.choice([True, False, None], n),
        "EXP": rng.integers(0, 50, n), "SEXP": rng.integers(0, 10, n), "LA": rng.integers(0, 500, n),
        "LD": rng.integers(0, 100, n), "NF": rng.integers(1, 20, n), "ND": rng.integers(1, 5, n),
        "semantic_risk_1to5": rng.choice([1, 3, None], n), "change_intent": "fix",
    })


def _feats(d):
    return [c for c in d.columns if c.startswith(("ah_", "rh", "xh_", "e_", "ar", "rf90_", "s_rel"))]


def test_cut():
    raw = _toy()
    cut = pd.Timestamp("2024-08-01", tz="UTC")
    full = F.prepare(raw).set_index("pr_number")
    early = raw[raw["created_at"] < cut].copy()
    # PRs still open at the cut: their close and outcome are unknown, so they close in the far future and no
    # feature may use their outcome (the prepare step needs some class, so it is scrambled)
    late = early["closed_at"] >= cut
    early.loc[late, "closed_at"] = pd.Timestamp("2099-01-01", tz="UTC")
    early.loc[late, "outcome_3class"] = np.random.default_rng(1).choice(F.CLASSES, int(late.sum()))
    part = F.prepare(early).set_index("pr_number")
    cols = _feats(part)
    a, b = full.loc[part.index, cols], part[cols]
    bad = [c for c in cols if not np.allclose(a[c].to_numpy(float), b[c].to_numpy(float), equal_nan=True)]
    assert not bad, bad


def test_open_prs_count_as_open():
    """A PR with no closed_at is open: it counts in ah_open_now and rh_open_now from the day it opened, as every
    open PR does when a new PR is scored, and it lends no outcome."""
    raw = pd.DataFrame({
        "repo": ["o/r"] * 3, "pr_number": [1, 2, 3], "author_email": ["a@x", "a@x", "b@x"],
        "created_at": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-05"], utc=True),
        "closed_at": pd.to_datetime([None, "2024-01-10", None], utc=True),
        "observed_through": pd.Timestamp("2024-06-01", tz="UTC"),
        "outcome_3class": ["goes_nowhere", "lands_clean", "goes_nowhere"], "author_is_bot": False,
        "author_confirmed_maintainer": False, "head_from_fork": True, "EXP": 1, "SEXP": 0, "LA": 10, "LD": 2,
        "NF": 1, "ND": 1, "semantic_risk_1to5": None, "change_intent": "fix"})
    d = F.prepare(raw).set_index("pr_number")
    assert d.loc[3, "rh_open_now"] == 2      # PR 1 (no close time) and PR 2 (closes later) are open on 5 January
    assert d.loc[2, "ah_open_now"] == 1      # the author's PR 1 is still open when PR 2 opens
    assert d.loc[3, "rh90_n"] == 0           # neither has lent an outcome yet


def test_counts():
    key = np.array([0, 0, 0, 1, -1])
    t = np.array([10, 20, 30, 20, 20])
    tc = np.array([15, 40, 35, 5, 1])
    s = F.prior_sum(key, t, tc, np.ones(5))[:, 0]
    assert s[0] == 0 and s[1] == 1 and s[2] == 1 and s[3] == 1 and np.isnan(s[4])


def test_seconds_any_unit():
    for unit in ("ns", "us", "ms", "s"):
        s = pd.Series(pd.to_datetime(["1970-01-02T00:00:00Z"]).as_unit(unit))
        assert F.secs(s)[0] == 86400, unit


def test_last_k():
    key = np.array([0, 0, 0, 0])
    t = np.array([10, 20, 30, 40])
    tc = np.array([11, 21, 31, 41])
    c, n = F.prior_last_k(key, t, tc, np.arange(4), 2)
    assert list(n) == [0, 1, 2, 2] and list(c[:, 0]) == [0, 0, 1, 3]


def test_sequences_cut():
    S = _seq()
    raw = _toy()
    cut = pd.Timestamp("2024-08-01", tz="UTC")
    early = raw[raw["created_at"] < cut].copy()
    late = early["closed_at"] >= cut
    early.loc[late, "closed_at"] = pd.Timestamp("2099-01-01", tz="UTC")
    early.loc[late, "outcome_3class"] = np.random.default_rng(1).choice(F.CLASSES, int(late.sum()))
    out = []
    for r in (raw, early):
        d = F.prepare(r)
        A, va, B, vb = S.build_tokens(d, S.token_table(d), S.author_sequences(d), S.repo_sequences(d))
        out.append((d["pr_number"].to_numpy(), A, B))
    (n1, A1, B1), (n2, A2, B2) = out
    pos = pd.Series(np.arange(len(n1)), index=n1).loc[n2].to_numpy()
    assert np.allclose(A1[pos], A2) and np.allclose(B1[pos], B2)


def test_stale_open_outcome_never_used():
    """A stale-open row (closed_at null: still open when mined, labelled at mining) must not lend its outcome to any
    PR's history or sequences: scrambling those outcomes changes no feature and no token."""
    S = _seq()
    raw = _toy()
    stale = np.random.default_rng(2).random(len(raw)) < 0.08
    raw.loc[stale, "closed_at"] = pd.NaT
    raw.loc[stale, "outcome_3class"] = "goes_nowhere"
    other = raw.copy()
    other.loc[stale, "outcome_3class"] = np.random.default_rng(3).choice(F.CLASSES, int(stale.sum()))
    out = []
    for r in (raw, other):
        d = F.prepare(r)
        A, va, B, vb = S.build_tokens(d, S.token_table(d), S.author_sequences(d), S.repo_sequences(d))
        out.append((d.set_index("pr_number"), A, B))
    (d1, A1, B1), (d2, A2, B2) = out
    cols = [c for c in _feats(d1) if c in d2]
    bad = [c for c in cols if not np.allclose(d1[c].to_numpy(float), d2[c].to_numpy(float), equal_nan=True)]
    assert not bad, bad
    # the sequence tokens hold each past PR's own at-open facts, which do not depend on its outcome; the
    # stale rows' own `y` sits only in the outcome slots, which must be masked
    assert np.allclose(A1, A2) and np.allclose(B1, B2)
