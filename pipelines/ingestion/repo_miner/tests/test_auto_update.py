"""doctor.auto_update keeps a checkout on the latest main, and only when that is safe."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import doctor  # noqa: E402


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), "-c", "user.email=t@t", "-c", "user.name=t", *args], check=True, capture_output=True)


@pytest.fixture
def repos(tmp_path, monkeypatch):
    """origin (bare) + a clone on main at version 1.0.0; origin/main then moves to 1.1.0 from a second clone."""
    monkeypatch.delenv("RIFFLE_NO_AUTO_UPDATE", raising=False)
    monkeypatch.delenv("RIFFLE_UPDATE_CHECKED", raising=False)
    origin, mine, other = tmp_path / "origin.git", tmp_path / "mine", tmp_path / "other"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(mine)], check=True, capture_output=True)
    (mine / "pipelines" / "miner").mkdir(parents=True)
    ver = mine / "pipelines" / "miner" / "version.py"
    ver.write_text('__version__ = "1.0.0"\n')
    git(mine, "add", "-A"); git(mine, "commit", "-qm", "v1"); git(mine, "branch", "-M", "main"); git(mine, "push", "-q", "-u", "origin", "main")
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True, capture_output=True)
    (other / "pipelines" / "miner" / "version.py").write_text('__version__ = "1.1.0"\n')
    git(other, "commit", "-qam", "v1.1"); git(other, "push", "-q", "origin", "main")
    return type("R", (), {"mine": mine, "ver": ver})


def run(r, **kw):
    out = []
    status = doctor.auto_update(root=str(r.mine), version_file=str(r.ver), reexec=False, say=out.append, **kw)
    os.environ.pop("RIFFLE_UPDATE_CHECKED", None)
    return status, out


def test_a_clean_checkout_behind_main_is_fast_forwarded(repos):
    status, out = run(repos)
    assert status == "updated" and "1.0.0 -> 1.1.0" in out[0]
    assert '"1.1.0"' in repos.ver.read_text()


def test_it_checks_once_per_launch(repos):
    assert doctor.auto_update(root=str(repos.mine), version_file=str(repos.ver), reexec=False, say=lambda m: None) == "updated"
    assert doctor.auto_update(root=str(repos.mine), version_file=str(repos.ver), reexec=False, say=lambda m: None) == "off"   # a child process


def test_uncommitted_changes_are_never_touched(repos):
    repos.ver.write_text('__version__ = "1.0.0"  # my edit\n')
    status, out = run(repos)
    assert status == "blocked" and "uncommitted changes" in out[0] and "my edit" in repos.ver.read_text()


def test_another_branch_is_left_alone(repos):
    git(repos.mine, "checkout", "-qb", "my-work")
    status, out = run(repos)
    assert status == "blocked" and "not main" in out[0] and '"1.0.0"' in repos.ver.read_text()


def test_a_checkout_ahead_of_main_is_a_developers_and_is_left_alone(repos):
    git(repos.mine, "fetch", "-q"); git(repos.mine, "merge", "-q", "--ff-only", "origin/main")
    (repos.mine / "x.txt").write_text("local\n"); git(repos.mine, "add", "-A"); git(repos.mine, "commit", "-qm", "local")
    status, _out = run(repos)
    assert status == "current"


def test_it_can_be_switched_off_and_a_missing_origin_is_not_an_error(repos, monkeypatch):
    monkeypatch.setenv("RIFFLE_NO_AUTO_UPDATE", "1")
    assert run(repos)[0] == "off" and '"1.0.0"' in repos.ver.read_text()
    monkeypatch.delenv("RIFFLE_NO_AUTO_UPDATE")
    git(repos.mine, "remote", "set-url", "origin", "/nonexistent/path.git")
    assert run(repos)[0] == "offline"                                                 # offline or unreachable: carry on with what is here
