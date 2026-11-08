"""The setup cache (setup_cache.py): the author index, concurrency and SZZ-relevant results are reused on a restart
only when the PR list and the clone's history are the same, and a reused result equals a computed one. Offline."""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
from datetime import datetime, timezone

import pytest

MINER = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MINER))

import gitio  # noqa: E402
import mine_repo  # noqa: E402
import setup_cache  # noqa: E402


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=a@b.c", "-c", "user.name=a", *args],
                   check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "clone"
    r.mkdir()
    _git(r, "init", "-q")
    (r / "f.txt").write_text("1\n")
    _git(r, "add", "f.txt")
    _git(r, "commit", "-q", "-m", "one")
    return r


def _prs():
    return [{"number": 1, "created_at": "2026-01-01T00:00:00Z", "merged_at": "2026-01-02T00:00:00Z",
             "closed_at": "2026-01-02T00:00:00Z", "merge_commit_sha": "a" * 40,
             "head": {"sha": "b" * 40}, "base": {"sha": "c" * 40}},
            {"number": 2, "created_at": "2026-01-03T00:00:00Z", "merged_at": None,
             "closed_at": "2026-01-04T00:00:00Z", "head": {"sha": "d" * 40}, "base": {"sha": "c" * 40},
             "_unmerged": True}]


def test_roundtrip_and_sections_are_independent(tmp_path, repo):
    path = str(tmp_path / "o_n.setup.json")
    prs = _prs()
    k1 = setup_cache.key_for("author_index", str(repo), prs)
    k2 = setup_cache.key_for("concurrency", str(repo), prs)
    assert k1 != k2
    assert setup_cache.load(path, "author_index", k1) is None            # no file yet
    assert setup_cache.save(path, "author_index", k1, {"x@y": ["2026-01-02T00:00:00+00:00"]})
    assert setup_cache.save(path, "concurrency", k2, {})
    assert setup_cache.load(path, "author_index", k1) == {"x@y": ["2026-01-02T00:00:00+00:00"]}
    assert setup_cache.load(path, "concurrency", k2) == {}               # an empty result is still a hit
    assert setup_cache.load(path, "author_index", k2) is None            # another section's key is a miss


@pytest.mark.parametrize("change", [
    lambda prs: prs[0].update(merged_at="2026-01-05T00:00:00Z"),
    lambda prs: prs[0]["head"].update(sha="e" * 40),
    lambda prs: prs[0].update(merge_commit_sha="f" * 40),
    lambda prs: prs[1].update(_unmerged=False),
    lambda prs: prs[0].update(_open={"head": "1" * 40, "base": "2" * 40}),
    lambda prs: prs.pop(),
])
def test_a_changed_pr_list_misses(repo, change):
    prs = _prs()
    before = setup_cache.key_for("concurrency", str(repo), prs)
    change(prs)
    assert setup_cache.key_for("concurrency", str(repo), prs) != before


def test_new_history_or_merge_style_misses(repo):
    prs = _prs()
    before = setup_cache.key_for("szz_relevant", str(repo), prs, "squash/normal")
    assert setup_cache.key_for("szz_relevant", str(repo), prs, "squash/normal") == before
    assert setup_cache.key_for("szz_relevant", str(repo), prs, "rebase") != before
    (repo / "f.txt").write_text("2\n")
    _git(repo, "commit", "-q", "-am", "two")                              # HEAD moved: a fetch or a new clone
    assert setup_cache.key_for("szz_relevant", str(repo), prs, "squash/normal") != before


def test_shallow_clone_misses(repo):
    prs = _prs()
    before = setup_cache.key_for("szz_relevant", str(repo), prs)
    (repo / ".git" / "shallow").write_text("")
    assert setup_cache.key_for("szz_relevant", str(repo), prs) != before


@pytest.mark.parametrize("content", ["", "{", "[]", json.dumps({"v": 999, "sections": {}}),
                                     json.dumps({"v": setup_cache.VERSION, "sections": []})])
def test_unreadable_or_foreign_file_is_a_miss(tmp_path, content):
    path = tmp_path / "o_n.setup.json"
    path.write_text(content)
    assert setup_cache.load(str(path), "author_index", "k") is None
    assert setup_cache.save(str(path), "author_index", "k", [1])          # and it is replaced, not fatal
    assert setup_cache.load(str(path), "author_index", "k") == [1]


def test_unwritable_path_is_not_fatal(tmp_path, capsys):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert setup_cache.save(str(blocker / "o_n.setup.json"), "s", "k", {}) is False
    assert "setup cache not written" in capsys.readouterr().err


def test_stage_runs_once_then_reuses_and_matches(tmp_path, repo):
    path = str(tmp_path / "o_n.setup.json")
    prs = _prs()
    calls = []
    idx = {"a@b.c": [datetime(2026, 1, 2, tzinfo=timezone.utc), datetime(2026, 1, 9, 5, 30, tzinfo=timezone.utc)]}

    def run():
        return mine_repo._setup_stage(
            path, "author_index", str(repo), prs, None, "author index",
            lambda: (calls.append(1) or idx, True),
            encode=lambda d: {e: [t.isoformat() for t in ts] for e, ts in d.items()},
            decode=lambda d: {e: [datetime.fromisoformat(t) for t in ts] for e, ts in d.items()})

    first, second = run(), run()
    assert len(calls) == 1
    assert first == second == idx


def test_a_result_with_errors_is_used_but_not_kept(tmp_path, repo):
    path = str(tmp_path / "o_n.setup.json")
    calls = []

    def run():
        return mine_repo._setup_stage(path, "concurrency", str(repo), _prs(), None, "concurrency",
                                      lambda: (calls.append(1) or {1: {}}, False))

    run(), run()
    assert len(calls) == 2


def test_undecodable_cache_is_recomputed(tmp_path, repo):
    path = str(tmp_path / "o_n.setup.json")
    key = setup_cache.key_for("concurrency", str(repo), _prs())
    setup_cache.save(path, "concurrency", key, {"not-an-int": {}})
    out = mine_repo._setup_stage(path, "concurrency", str(repo), _prs(), None, "concurrency",
                                 lambda: ({7: {"concurrent_open_prs_same_files": 0,
                                               "concurrent_open_prs_same_hunks": 0}}, True),
                                 decode=lambda d: {int(n): v for n, v in d.items()})
    assert list(out) == [7]


def test_concurrency_reports_unreadable_diffs(monkeypatch, repo):
    prs = [{"number": 1, "created_at": "2026-01-01T00:00:00Z", "merged_at": "2026-01-02T00:00:00Z",
            "merge_commit_sha": "a" * 40, "head": {"sha": "b" * 40}, "base": {"sha": "c" * 40}}]

    def boom(*a, **k):
        raise gitio.GitError("nope")

    monkeypatch.setattr(mine_repo, "_pr_diff_range", lambda repo, pr, resolver=None: ("c" * 40, "b" * 40))
    monkeypatch.setattr(gitio, "changed_lines_by_file", boom)
    stats: dict = {}
    mine_repo._build_concurrency(str(repo), prs, None, 1, stats=stats)
    assert stats == {"errors": 1}
    monkeypatch.setattr(gitio, "changed_lines_by_file", lambda *a: {"f": {1}})
    stats = {}
    out = mine_repo._build_concurrency(str(repo), prs, None, 1, stats=stats)
    assert stats == {"errors": 0} and out[1]["concurrent_open_prs_same_files"] == 0
