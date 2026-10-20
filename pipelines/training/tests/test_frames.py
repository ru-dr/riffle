"""frames.py: the shared loader."""
from __future__ import annotations

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import frames  # noqa: E402


def _tables(tmp_path, n_prs=60, version="6.2.0", snap_mined_at="t1"):
    rows, snaps, commits = [], [], []
    for i in range(1, n_prs + 1):
        merged = i % 3 != 0
        rows.append({"repo": f"o/r{i % 4}", "pr_number": i, "label_merged": merged,
                     "label_bad": i % 7 == 0, "docs_only": i % 2 == 0, "label_bad_observed": i % 5 != 0, "LA": 10 + i,
                     "open_snapshot": "final" if i % 10 == 0 else "exact", "outcome_3class": "lands_clean",
                     "miner_version": version, "mined_at": "t1", "head_sha": f"h{i}"})
        for j in range(3):
            snaps.append({**rows[-1], "mined_at": snap_mined_at, "snapshot_id": f"{i}@{j}", "snapshot_index": j,
                          "snapshot_head_sha": f"s{i}{j % 2}", "near_decided": j == 2, "snapshot_weight": 1 / 3,
                          "trigger": "open" if j == 0 else "clock"})
        commits.append({"repo": f"o/r{i % 4}", "pr_number": i, "head_sha": "s%d1" % i, "base_sha": "b",
                        "first_seen": "x", "at_open": False, "error": None, "LA": 99})
    base = str(tmp_path / "dataset.jsonl")
    pd.DataFrame(rows).to_json(base, orient="records", lines=True)
    pd.DataFrame(snaps).to_json(str(tmp_path / "dataset_snapshots.jsonl"), orient="records", lines=True)
    pd.DataFrame(commits).to_json(str(tmp_path / "dataset_commit_features.jsonl"), orient="records", lines=True)
    return base


def test_loads_siblings_and_builds_both_frames(tmp_path):
    f = frames.build_frames(frames.load_tables(_tables(tmp_path)))
    assert set(f.breakage_train.label_merged) == {True} and f.breakage_train.label_bad_observed.all()
    assert not f.outcome_train.near_decided.any() and not f.outcome_test.near_decided.any()
    assert len(f.breakage_train) + len(f.breakage_test) == sum(1 for i in range(1, 61) if i % 3 and i % 5)


def test_a_pr_is_on_one_side_in_every_frame_and_snapshots_stay_together(tmp_path):
    f = frames.build_frames(frames.load_tables(_tables(tmp_path)), by="pr")
    key = lambda d: set(zip(d.repo, d.pr_number))                                # noqa: E731
    assert not key(f.outcome_train) & key(f.outcome_test)
    assert not key(f.breakage_train) & key(f.outcome_test)                       # same hash across tables
    assert not key(f.breakage_test) & key(f.outcome_train)
    r = frames.build_frames(frames.load_tables(_tables(tmp_path)), by="repo")
    assert not set(r.outcome_train.repo) & set(r.outcome_test.repo)


def test_split_is_stable_between_runs(tmp_path):
    t = frames.load_tables(_tables(tmp_path))
    a = frames.build_frames(t, seed=3).outcome_test[["repo", "pr_number"]]
    b = frames.build_frames(t, seed=3).outcome_test[["repo", "pr_number"]]
    pd.testing.assert_frame_equal(a, b)


def test_final_open_snapshot_has_diff_features_nulled(tmp_path):
    f = frames.build_frames(frames.load_tables(_tables(tmp_path)))
    d = pd.concat([f.breakage_train, f.breakage_test])
    assert d[d.open_snapshot == "final"].LA.isna().all() and d[d.open_snapshot == "exact"].LA.notna().all()
    assert d[d.open_snapshot == "final"].docs_only.isna().all() and d[d.open_snapshot == "exact"].docs_only.notna().all()
    s = pd.concat([f.outcome_train, f.outcome_test])
    assert s[s.open_snapshot == "final"].LA.isna().all()


def test_inputs_exclude_outcomes_ids_and_snapshot_bookkeeping(tmp_path):
    f = frames.build_frames(frames.load_tables(_tables(tmp_path)))
    ins = f.inputs(f.outcome_train)
    for bad in ("label_bad", "outcome_3class", "snapshot_weight", "near_decided", "repo", "pr_number",
                "snapshot_id", "mined_at"):
        assert bad not in ins
    assert "LA" in ins


def test_stale_snapshots_are_refused_unless_told_not_to(tmp_path):
    base = _tables(tmp_path, snap_mined_at="t0")
    with pytest.raises(ValueError, match="no longer match"):
        frames.load_tables(base)
    assert frames.load_tables(base, check_fresh=False).snapshots is not None


def test_commit_features_join_is_left_and_prefixed(tmp_path):
    t = frames.load_tables(_tables(tmp_path))
    f = frames.build_frames(t, with_commit_features=True)
    s = pd.concat([f.outcome_train, f.outcome_test])
    assert len(s) == len(t.snapshots[t.snapshots.near_decided != True]) and "commit_LA" in s.columns
    assert s.commit_LA.notna().any() and s.commit_LA.isna().any()            # left join keeps unmatched heads


def test_weights_add_up_to_one_per_pr_after_filtering(tmp_path):
    f = frames.build_frames(frames.load_tables(_tables(tmp_path)))
    for d in (f.outcome_train, f.outcome_test):
        sums = d.groupby(["repo", "pr_number"]).snapshot_weight.sum()
        assert (sums.round(6) == 1.0).all()
    raw = frames.build_frames(frames.load_tables(_tables(tmp_path)), renormalize_weights=False)
    assert raw.outcome_train.groupby(["repo", "pr_number"]).snapshot_weight.sum().round(3).iloc[0] == 0.667
