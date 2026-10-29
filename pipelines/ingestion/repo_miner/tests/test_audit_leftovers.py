"""Audit leftovers: the git stop hook, rename-aware SZZ lines, and the LLM health rule."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import gitio  # noqa: E402
import health  # noqa: E402
import labels  # noqa: E402
import mine_repo  # noqa: E402
from features.llm_flags import FLAG_SCHEMA  # noqa: E402


def git(repo, *args, when="2024-01-01T00:00:00Z"):
    env = {**os.environ, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when, "GIT_AUTHOR_NAME": "t",
           "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    return subprocess.run(["git", "-C", repo, *args], check=True, env=env, capture_output=True,
                          text=True).stdout.strip()


def write(repo, name, text):
    with open(os.path.join(repo, name), "w", encoding="utf-8") as fh:
        fh.write(text)


@pytest.fixture
def repo(tmp_path):
    r = str(tmp_path)
    git(r, "init", "-q", "-b", "main")
    return r


@pytest.fixture(autouse=True)
def _clear_stop():
    gitio.STOP.clear()
    yield
    gitio.STOP.clear()


# ---- the stop hook -------------------------------------------------------------

def test_stop_is_one_event_shared_with_mine_repo():
    assert mine_repo.STOP is gitio.STOP
    assert mine_repo.Stopped is gitio.GitStopped
    assert not issubclass(gitio.GitStopped, gitio.GitError)     # never swallowed as a git failure


def test_a_stop_refuses_every_git_launcher(repo):
    write(repo, "a.txt", "x\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "c")
    head = git(repo, "rev-parse", "HEAD")
    assert gitio.rev_parse(repo, "HEAD") == head                # runs while clear
    mine_repo.STOP.set()
    with pytest.raises(mine_repo.Stopped):
        gitio.rev_parse(repo, "HEAD")
    with pytest.raises(mine_repo.Stopped):
        gitio.rev_parse_safe(repo, "HEAD")
    with pytest.raises(mine_repo.Stopped):
        gitio.existing_commits(repo, [head])
    with pytest.raises(mine_repo.Stopped):
        gitio.read_blob(repo, f"{head}:a.txt")
    with pytest.raises(mine_repo.Stopped):
        list(gitio._stream_numstat_log(repo, "log", "--numstat"))
    with pytest.raises(mine_repo.Stopped):
        gitio.run_with_stall_watchdog([sys.executable, "-c", "pass"])
    mine_repo.STOP.clear()
    assert gitio.rev_parse(repo, "HEAD") == head                # a later run is not poisoned


def test_a_stop_ends_a_worker_at_its_next_git_call(repo):
    write(repo, "a.txt", "x\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "c")
    calls = []

    def worker():
        calls.append(gitio.rev_parse(repo, "HEAD"))
        mine_repo.STOP.set()                    # the Stop arrives between two git calls
        calls.append(gitio.rev_parse(repo, "HEAD"))

    with pytest.raises(mine_repo.Stopped):
        worker()
    assert len(calls) == 1


# ---- rename-aware old lines ----------------------------------------------------

def test_many_gives_the_old_lines_of_a_renamed_and_edited_file(repo):
    body = "".join(f"line {i}\n" for i in range(30))
    write(repo, "old.py", body)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "add", when="2024-01-01T00:00:00Z")
    intro = git(repo, "rev-parse", "HEAD")
    write(repo, "other.py", "a\nb\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "other", when="2024-01-02T00:00:00Z")
    parent = git(repo, "rev-parse", "HEAD")
    git(repo, "mv", "old.py", "new.py")
    write(repo, "new.py", body.replace("line 3\n", "line three\n"))
    write(repo, "other.py", "a\nB\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "fix: rename and edit", when="2024-01-10T00:00:00Z")
    fix = git(repo, "rev-parse", "HEAD")
    assert gitio.deleted_or_modified_lines_many(repo, parent, fix, ["new.py", "other.py"]) == {
        "new.py": [4], "other.py": [2]}
    # the single-path call still hides a rename's source unless told its old name
    assert gitio.deleted_or_modified_lines(repo, parent, fix, "new.py") == []
    # SZZ blames the old name at the parent and finds the commit that wrote the line
    sha, by_path, _calls, fails = labels._szz_fix_bugs(repo, fix, parent, ["new.py"])
    assert by_path == {"new.py": [intro]} and fails == 0


# ---- health: LLM rows that have nothing to judge --------------------------------


def test_health_empty_and_skipped_rows_are_in_scope_but_not_missing():
    key = next(iter(FLAG_SCHEMA))
    full = {"pr_number": 1, key: True}
    empty = {"pr_number": 2, key: None}
    skipped = {"pr_number": 3, key: None}
    failed = {"pr_number": 4, key: None}
    status = {2: "empty", 3: "skipped", 4: "fail"}
    h = health.check([full, empty, skipped, failed], in_llm_scope=lambda r: True,
                     llm_nothing_to_judge=lambda r: status.get(r["pr_number"]) in ("empty", "skipped"))
    s = h["stats"]
    assert s["llm_rows"] == 4                           # none silently excluded
    assert s["llm_nothing_to_judge"] == 2
    assert s["llm_missing_share"] == 0.5                # 1 failed of 2 owed, not 3 of 4


def test_health_without_the_exemption_keeps_the_old_answer():
    key = next(iter(FLAG_SCHEMA))
    h = health.check([{key: True}, {key: None}], in_llm_scope=lambda r: True)
    assert h["stats"]["llm_rows"] == 2 and h["stats"]["llm_missing_share"] == 0.5


# ---- a failed blame is unobserved, not a measured zero ----------------------------


def test_timed_out_blame_is_null_and_not_stored_by_szz(monkeypatch, tmp_path):
    from features import line_history
    repo = str(tmp_path)
    monkeypatch.setattr(gitio, "_blame", lambda *a, **k: (setattr(gitio._BLAME_TL, "failed", True), {})[1])
    monkeypatch.setattr(gitio, "deleted_or_modified_lines", lambda *a, **k: [3])
    out = line_history.per_file_line_features(
        repo, "b", "h", "f.py", "a@x", {"base_time": None}, config.Settings())
    assert all(v is None for v in out.values())
    monkeypatch.setattr(gitio, "deleted_or_modified_lines_many", lambda *a, **k: {"f.py": [3]})
    monkeypatch.setattr(gitio, "rename_map", lambda *a, **k: {})
    monkeypatch.setattr(gitio, "blame_lines", lambda *a, **k: (setattr(gitio._BLAME_TL, "failed", True), {})[1])
    _sha, by_path, _c, _f = labels._szz_fix_bugs(repo, "s", "p", ["f.py"])
    assert by_path == {}
