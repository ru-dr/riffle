"""Finished repos whose captures were never compiled are found (and only those), so a rerun can compile them."""
from __future__ import annotations

import json
import pathlib
import sys

MINER = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MINER))

import snapshot_capture as cap  # noqa: E402
import snapshots as S  # noqa: E402


def _capture(root, folder, text='{"repo": "x"}\n'):
    d = root / folder
    d.mkdir(parents=True)
    (d / cap.CAPTURE_FILE).write_text(text)


def _table(path, repos):
    path.write_text("".join(json.dumps({"repo": r}) + "\n" for r in repos))
    return str(path)


def test_lists_only_captured_repos_without_rows(tmp_path):
    raw = tmp_path / "raw"
    _capture(raw, "Own_has-rows")
    _capture(raw, "Own_missing")
    _capture(raw, "Own_empty", text="")
    out = _table(tmp_path / "d_snapshots.jsonl", ["own/has-rows"])
    commits = _table(tmp_path / "d_commit_features.jsonl", ["own/has-rows"])
    done = {"own/has-rows", "own/missing", "own/empty", "own/no-capture"}
    assert S.uncompiled_repos(done, str(raw), out, commits) == ["own/missing"]


def test_repo_missing_from_either_table_is_listed(tmp_path):
    raw = tmp_path / "raw"
    _capture(raw, "own_a")
    out = _table(tmp_path / "s.jsonl", ["own/a"])
    assert S.uncompiled_repos({"own/a"}, str(raw), out, str(tmp_path / "none.jsonl")) == ["own/a"]


def test_nothing_to_do_without_captures_or_done_repos(tmp_path):
    assert S.uncompiled_repos(set(), str(tmp_path), "a", "b") == []
    assert S.uncompiled_repos({"own/a"}, str(tmp_path / "missing"), "a", "b") == []
