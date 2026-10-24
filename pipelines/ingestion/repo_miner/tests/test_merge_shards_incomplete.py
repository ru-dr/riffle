"""merge_shards.py: shards missing repos, case-different repos, empty tables."""
from __future__ import annotations

import json
import os
import sys

import pytest

pd = pytest.importorskip("pandas")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import merge_shards  # noqa: E402
import naming  # noqa: E402
from test_merge_shards import _shard  # noqa: E402


def _edit_manifest(path_out, **changes):
    path = naming.manifest_path(path_out)
    m = json.load(open(path))
    m.update(changes)
    json.dump(m, open(path, "w"))


def _merge(tmp, **kw):
    return merge_shards.merge(str(tmp / "dataset.jsonl"), str(tmp), **kw)


def test_refuses_a_manifest_with_failed_repos_unless_allowed(tmp_path):
    a = _shard(tmp_path, 1, 2, "o/a")
    _shard(tmp_path, 2, 2, "o/b")
    _edit_manifest(a, failed=["o/z"])
    with pytest.raises(SystemExit, match="failed on 1 repo"):
        _merge(tmp_path)
    assert not (tmp_path / "dataset_merged.jsonl").exists()
    assert _merge(tmp_path, allow_incomplete=True)["rows"]["dataset"] == 4


def test_refuses_a_dataset_that_lacks_a_listed_repo(tmp_path):
    a = _shard(tmp_path, 1, 2, "o/a")
    _shard(tmp_path, 2, 2, "o/b")
    _edit_manifest(a, repos=["o/a", "o/missing"])
    with pytest.raises(SystemExit, match="no rows for 1 repo"):
        _merge(tmp_path)
    assert _merge(tmp_path, allow_incomplete=True)["rows"]["dataset"] == 4


def test_refuses_rows_of_a_repo_the_manifest_does_not_list(tmp_path):
    a = _shard(tmp_path, 1, 2, "o/a")
    _shard(tmp_path, 2, 2, "o/b")
    _edit_manifest(a, repos=["o/other"])
    with pytest.raises(SystemExit, match="does not list"):
        _merge(tmp_path, allow_incomplete=True)


def test_repo_names_are_compared_in_lower_case_across_pcs(tmp_path):
    _shard(tmp_path, 1, 2, "O/Repo")
    _shard(tmp_path, 2, 2, "o/repo")
    with pytest.raises(SystemExit, match="is on PC 1 and PC 2"):
        _merge(tmp_path)


def test_empty_tables_without_columns_merge(tmp_path):
    for k, repo in ((1, "o/a"), (2, "o/b")):
        out = _shard(tmp_path, k, 2, repo)
        snap, commits = naming.snapshot_outs(out)
        open(snap, "w").close()                       # a PC that built no snapshot rows
        open(commits, "w").close()
    r = _merge(tmp_path)
    assert r["rows"]["dataset"] == 4 and r["rows"]["snapshots"] == 0 and r["rows"]["commit_features"] == 0
