"""
consistency.py — do a dataset and the snapshot table built from it still match?

Every snapshot row carries the columns of the at-open row it was built from.
If the dataset is re-mined afterwards without rebuilding the snapshots, those
copies go stale and nothing else would say so. A re-mine changes `mined_at`
(and maybe `miner_version`, the at-open `head_sha` or the outcome), so comparing
those per PR catches it.

pipelines/training/frames.py carries the same check; the two trees don't share
code, so keep them in step (tests/test_consistency.py and training's test_frames.py
use the same cases).
"""
from __future__ import annotations

KEY = ("repo", "pr_number")
CHECKED = ("miner_version", "mined_at", "head_sha", "outcome_3class")


def stale_prs(dataset, snapshots) -> dict:
    """{"missing": PRs in the snapshots but not the dataset, "changed": PRs whose
    copied columns differ}, as lists of (repo, pr_number). Both empty means the
    tables match. `dataset` and `snapshots` are pandas DataFrames."""
    cols = [c for c in CHECKED if c in dataset.columns and c in snapshots.columns]
    snap = snapshots.drop_duplicates(list(KEY)).set_index(list(KEY))[cols]
    data = dataset.drop_duplicates(list(KEY)).set_index(list(KEY))[cols]
    missing = snap.index.difference(data.index)
    both = snap.index.intersection(data.index)
    a, b = snap.loc[both], data.loc[both]
    differs = ~((a == b) | (a.isna() & b.isna())).all(axis=1)
    return {"missing": list(missing), "changed": list(both[differs.to_numpy()])}
