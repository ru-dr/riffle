"""mine_repo's wiring of the audit fixes: fast-forwarded merge heads (M24), renames, as-of CVE,
the per-PR file cap, the LLM provenance, szz_window_closed, SZZ across a rename, and stop (M37).

Offline: throwaway git repos, the feature modules replaced by recording stubs where a call is
what is being checked."""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import time

import pytest

MINER = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MINER))

import config  # noqa: E402
import gitio  # noqa: E402
import labels  # noqa: E402
import mine_repo  # noqa: E402
from features import history, llm_flags  # noqa: E402


def _git(repo, *args, date="2025-03-01T00:00:00Z", author="A <a@example.com>"):
    name, _, mail = author.partition(" <")
    env = dict(os.environ, GIT_AUTHOR_NAME=name, GIT_AUTHOR_EMAIL=mail.rstrip(">"),
               GIT_COMMITTER_NAME=name, GIT_COMMITTER_EMAIL=mail.rstrip(">"),
               GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    return subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True,
                          text=True).stdout.strip()


def _settings(tmp_path, **kw):
    st = config.Settings()
    st.enable_llm = False
    st.enable_github_api = False
    st.enable_deps_cve = False
    st.pr_discovery = "git"
    st.min_age_days = 0
    st.workers = 2
    st.enable_snapshots = False
    st.ci_cache_dir = str(tmp_path / "cache")
    for k, v in kw.items():
        setattr(st, k, v)
    return st


# ---- M24 ----------------------------------------------------------------------

def test_fast_forwarded_pr_with_a_merge_head_is_diffed_on_its_own_side(tmp_path):
    r = tmp_path / "ff"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    (r / "base.txt").write_text("x\n")
    _git(r, "add", "."); _git(r, "commit", "-qm", "init", date="2025-01-01T00:00:00Z")
    _git(r, "checkout", "-qb", "feature")
    (r / "feat.py").write_text("def f():\n    return 1\n")
    _git(r, "add", "."); _git(r, "commit", "-qm", "add feat", date="2025-01-02T00:00:00Z",
                              author="Alice <alice@example.com>")
    b = _git(r, "rev-parse", "HEAD")
    _git(r, "checkout", "-q", "main")
    (r / "main.txt").write_text("m\n")
    _git(r, "add", "."); _git(r, "commit", "-qm", "main work", date="2025-01-03T00:00:00Z",
                              author="Maint <maint@example.com>")
    m1 = _git(r, "rev-parse", "HEAD")
    _git(r, "checkout", "-q", "feature")
    _git(r, "merge", "-q", "--no-ff", "-m", "merge main", "main", date="2025-01-04T00:00:00Z",
         author="Maint <maint@example.com>")
    h = _git(r, "rev-parse", "HEAD")
    _git(r, "checkout", "-q", "main")
    _git(r, "merge", "-q", "--ff-only", "feature")
    assert _git(r, "rev-parse", "main") == h and len(gitio.commit_parents(str(r), h)) == 2
    pr = {"number": 5, "merge_commit_sha": h, "head": {"sha": h}, "base": {"sha": m1}}
    base, head = mine_repo._pr_diff_range(str(r), pr)
    assert (base, head) == (m1, h)
    assert gitio.changed_files(str(r), base, head) == ["feat.py"]       # not main.txt
    assert mine_repo._feat_head(str(r), pr) == b                         # Alice's, not the merger's


# ---- wiring through mine_repo ---------------------------------------------------

@pytest.fixture()
def rename_repo(tmp_path):
    r = tmp_path / "rn"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    body = "".join(f"def f{i}():\n    return {i}\n" for i in range(20))
    (r / "old_name.py").write_text(body)
    (r / "other.py").write_text(body)
    _git(r, "add", "."); _git(r, "commit", "-qm", "init", date="2025-01-01T00:00:00Z")
    _git(r, "mv", "old_name.py", "new_name.py")
    with open(r / "other.py", "a") as fh:
        fh.write("def extra():\n    return 0\n")
    _git(r, "add", "."); _git(r, "commit", "-qm", "rename and edit (#1)", date="2025-02-01T00:00:00Z")
    return r


def test_renames_as_of_and_the_file_cap_reach_the_features(rename_repo, tmp_path, monkeypatch):
    seen = {"per_file": {}, "deps": [], "scan": [], "api": []}
    real = history.per_file_features

    def per_file(repo, base, path, idx, st, old_path=None):
        seen["per_file"][path] = old_path
        return real(repo, base, path, idx, st, old_path=old_path)
    monkeypatch.setattr(history, "per_file_features", per_file)
    monkeypatch.setattr(mine_repo.deps_cve, "compute",
                        lambda *a, **k: seen["deps"].append(k) or {})
    monkeypatch.setattr(mine_repo.scanners, "compute",
                        lambda *a, **k: seen["scan"].append(k) or {})
    monkeypatch.setattr(mine_repo.api_contract, "compute",
                        lambda *a, **k: seen["api"].append(k) or {})
    rows = mine_repo.mine_repo(str(rename_repo), "ex", "rn", _settings(tmp_path),
                               out_path=str(tmp_path / "o.jsonl"))
    assert len(rows) == 1
    assert seen["per_file"]["new_name.py"] == "old_name.py"
    assert seen["per_file"]["other.py"] is None
    for k in (seen["deps"][0], seen["scan"][0], seen["api"][0]):
        assert k["renames"] == {"new_name.py": "old_name.py"}
    assert seen["deps"][0]["as_of"]                                   # the PR's open time
    # renamed, but known by its old name: its history and size are read, not zero
    assert rows[0]["LT"] and rows[0]["LT"] > 0
    assert rows[0]["szz_window_closed"] in (True, False)


def test_a_pr_past_the_file_cap_has_null_sums_and_sizes_not_zeros(rename_repo, tmp_path):
    pytest.importorskip("lizard")   # complexity columns need lizard; installed in CI's pipelines job
    rows = mine_repo.mine_repo(str(rename_repo), "ex", "rn",
                               _settings(tmp_path, max_files_per_pr_deep=1),
                               out_path=str(tmp_path / "o.jsonl"))
    r = rows[0]
    assert r["NF"] == 2
    assert r["LT"] is None and r["la_norm"] is None and r["ld_norm"] is None
    assert r["sum_file_complexity_ccn"] is None
    assert r["max_file_complexity_ccn"] is not None                  # measured on the file that was


def test_no_network_author_confirmed_maintainer_is_null(rename_repo, tmp_path):
    rows = mine_repo.mine_repo(str(rename_repo), "ex", "rn", _settings(tmp_path),
                               out_path=str(tmp_path / "o.jsonl"))
    assert rows[0]["author_confirmed_maintainer"] is None


def test_llm_provenance_and_diff_truncated_come_from_the_call(rename_repo, tmp_path, monkeypatch):
    got = {}

    def fetch(repo, base, head, st, skip=None):
        return {"status": "ok", "raw": {k: None for k in llm_flags.FLAG_SCHEMA}, "secs": 0.0,
                "model": "claude-test-1", "diff_truncated": True}
    monkeypatch.setattr(llm_flags, "fetch", fetch)
    monkeypatch.setattr(llm_flags, "load_reuse", lambda path, st=None: got.update(st=st) or 0)
    st = _settings(tmp_path, enable_llm=True)
    os.makedirs(st.ci_cache_dir)
    pathlib.Path(st.ci_cache_dir, "ex_rn.llm_reuse.json").write_text("{}")
    rows = mine_repo.mine_repo(str(rename_repo), "ex", "rn", st, out_path=str(tmp_path / "o.jsonl"))
    assert rows[0]["llm_model"] == "claude-test-1"
    assert rows[0]["llm_diff_truncated"] is True
    assert got["st"] is st                                           # reuse is matched on this run's prompt and model
    assert mine_repo.LAST_LLM_STATUS == {1: "ok"}


# ---- SZZ across a rename ----------------------------------------------------------

def test_szz_blames_the_old_name_of_a_renamed_file(tmp_path):
    r = tmp_path / "szz"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    body = "".join(f"line {i}\n" for i in range(30))
    (r / "old.txt").write_text(body)
    _git(r, "add", "."); _git(r, "commit", "-qm", "add file", date="2025-01-01T00:00:00Z")
    intro = _git(r, "rev-parse", "HEAD")
    _git(r, "mv", "old.txt", "new.txt")
    (r / "new.txt").write_text(body.replace("line 10\n", "line ten fixed\n"))
    _git(r, "add", "."); _git(r, "commit", "-qm", "fix: rename and repair", date="2025-02-01T00:00:00Z")
    fix = _git(r, "rev-parse", "HEAD")
    sha, by_path, _calls, _fails = labels._szz_fix_bugs(str(r), fix, f"{fix}^", ["new.txt"])
    assert by_path == {"new.txt": [intro]}


# ---- stop (M37) ----------------------------------------------------------------------

def test_stop_ends_a_pr_at_its_next_step_and_kills_children():
    mine_repo.STOP.clear()
    mine_repo._step("diff")                                           # fine while not stopped
    child = subprocess.Popen(["sleep", "60"], start_new_session=True)  # like gitio's children
    try:
        mine_repo.STOP.set()
        with pytest.raises(mine_repo.Stopped):
            mine_repo._step("per_file")
        mine_repo._kill_children()
        t0 = time.monotonic()
        assert child.wait(timeout=5) == -9 and time.monotonic() - t0 < 5
    finally:
        mine_repo.STOP.clear()
        if child.poll() is None:
            child.kill()
