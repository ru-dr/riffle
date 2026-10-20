"""merge_shards.py and consistency.py."""
from __future__ import annotations

import json
import os
import sys

import pytest

pd = pytest.importorskip("pandas")          # optional dependency; CI installs only the core test set

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import consistency  # noqa: E402
import merge_shards  # noqa: E402
import naming  # noqa: E402


def _shard(tmp, k, n, repo, split="abc123", version="6.2.0", snapshots=True, mined_at="2026-10-19T00:00:00Z"):
    out = str(tmp / f"dataset_pc{k}of{n}.jsonl")
    ds = pd.DataFrame([{"repo": repo, "pr_number": i, "miner_version": version, "mined_at": mined_at,
                        "head_sha": f"h{i}", "outcome_3class": "lands_clean"} for i in (1, 2)])
    ds.to_json(out, orient="records", lines=True)
    snap_out, commits_out = naming.snapshot_outs(out)
    files = {"dataset": os.path.basename(out), "snapshots": None, "commit_features": None}
    if snapshots:
        sn = ds.assign(snapshot_id=[f"{repo}#{i}@0" for i in (1, 2)])
        sn.to_json(snap_out, orient="records", lines=True)
        pd.DataFrame([{"repo": repo, "pr_number": 1, "head_sha": "h1"}]).to_json(commits_out, orient="records", lines=True)
        files.update(snapshots=os.path.basename(snap_out), commit_features=os.path.basename(commits_out))
    with open(naming.manifest_path(out), "w") as fh:
        json.dump({"split_id": split, "pc": k, "of": n, "miner_version": version, "repos": [repo],
                   "files": files}, fh)
    return out


def test_merges_each_table_and_writes_a_report(tmp_path):
    _shard(tmp_path, 1, 2, "o/a")
    _shard(tmp_path, 2, 2, "o/b")
    out = str(tmp_path / "dataset.jsonl")
    r = merge_shards.merge(out, str(tmp_path))
    assert r["rows"] == {"dataset": 4, "snapshots": 4, "commit_features": 2}
    assert len(pd.read_json(str(tmp_path / "dataset_merged.jsonl"), lines=True)) == 4
    assert (tmp_path / "dataset_merged_snapshots.jsonl").exists()
    assert json.load(open(tmp_path / "dataset_merged.merge.json"))["split_id"] == "abc123"


@pytest.mark.parametrize("make, expect", [
    (lambda t: (_shard(t, 1, 3, "o/a"), _shard(t, 2, 3, "o/b")), "missing [3]"),
    (lambda t: (_shard(t, 1, 2, "o/a"), _shard(t, 2, 2, "o/b", split="zzz")), "split IDs differ"),
    (lambda t: (_shard(t, 1, 2, "o/a"), _shard(t, 2, 2, "o/b", version="6.1.1")), "miner versions differ"),
    (lambda t: (_shard(t, 1, 2, "o/a"), _shard(t, 2, 2, "o/a")), "is on PC 1 and PC 2"),
    (lambda t: (_shard(t, 1, 2, "o/a"), _shard(t, 2, 2, "o/b", snapshots=False)), "some PCs have a snapshots"),
])
def test_refuses_pieces_that_do_not_fit(tmp_path, make, expect):
    make(tmp_path)
    with pytest.raises(SystemExit) as e:
        merge_shards.merge(str(tmp_path / "dataset.jsonl"), str(tmp_path))
    assert expect in str(e.value)
    assert not (tmp_path / "dataset_merged.jsonl").exists()


def test_refuses_snapshots_older_than_the_dataset(tmp_path):
    a = _shard(tmp_path, 1, 2, "o/a")
    _shard(tmp_path, 2, 2, "o/b")
    ds = pd.read_json(a, lines=True)
    ds["mined_at"] = "2026-10-20T00:00:00Z"                 # dataset re-mined after the snapshots were built
    ds.to_json(a, orient="records", lines=True)
    with pytest.raises(SystemExit) as e:
        merge_shards.merge(str(tmp_path / "dataset.jsonl"), str(tmp_path))
    assert "no longer match" in str(e.value)


def test_stale_prs_cases():
    d = pd.DataFrame([{"repo": "o/a", "pr_number": 1, "miner_version": "6.2.0", "mined_at": "t1",
                       "head_sha": "h", "outcome_3class": "x"}])
    assert consistency.stale_prs(d, d) == {"missing": [], "changed": []}
    assert consistency.stale_prs(d.assign(mined_at="t2"), d)["changed"] == [("o/a", 1)]
    assert consistency.stale_prs(d.assign(pr_number=9), d)["missing"] == [("o/a", 1)]


def test_batch_writes_a_manifest_that_merge_shards_accepts(tmp_path):
    import argparse
    import build_dataset
    for k, repo in ((1, "o/a"), (2, "o/b")):
        out = tmp_path / f"dataset_pc{k}of2.jsonl"
        pd.DataFrame([{"repo": repo, "pr_number": 1}]).to_json(str(out), orient="records", lines=True)
        args = argparse.Namespace(test=False, shard=f"{k}/2", out=str(out), split_id="s1")
        build_dataset._write_shard_manifest(args, [(repo.split("/")[0], repo.split("/")[1], None)], 1, [])
    shards = merge_shards.find_shards(str(tmp_path / "dataset.jsonl"), str(tmp_path))
    assert [s["pc"] for s in shards] == [1, 2] and shards[0]["split_id"] == "s1"
    assert merge_shards.check_manifests(shards) == []
    assert shards[0]["files"]["snapshots"] is None
