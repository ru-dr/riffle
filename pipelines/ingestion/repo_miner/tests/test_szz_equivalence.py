"""
SZZ equivalence: the fast build_szz_index (-L ranged blame, process pool,
JSONL checkpoint) must produce exactly the index the original serial
full-file-blame loop produced, keys and list order included.

Runs on a small synthetic repo by default. Point SZZ_EQUIV_REPO at a real
clone to check one too (slow on big repos):
    SZZ_EQUIV_REPO=cloned_repos/pallets_flask python -m pytest tests/test_szz_equivalence.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import gitio  # noqa: E402
import labels  # noqa: E402


def reference_szz_index(repo: str) -> dict:
    """The original serial loop: full-file blame at the fix's parent."""
    index: dict = {}
    for c, rows in gitio.iter_commits_with_files(repo, "HEAD"):
        if not c.parents or not config.is_fix_text(c.subject):
            continue
        parent = c.parents[0]
        fix_time = c.committed.astimezone(timezone.utc).isoformat()
        for _a, _d, path in rows:
            old_lines = gitio.deleted_or_modified_lines(repo, parent, c.sha, path)
            if not old_lines:
                continue
            blame = gitio.blame_file(repo, parent, path)
            for ln in old_lines:
                bl = blame.get(ln)
                if bl and bl.orig_commit:
                    fixes = index.setdefault(bl.orig_commit, [])
                    if (c.sha, fix_time) not in fixes:
                        fixes.append((c.sha, fix_time))
    return index


def _git(repo, *args, env=None):
    subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True, env=env)


def _commit(repo, msg, n, files):
    for name, text in files.items():
        p = os.path.join(repo, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            fh.write(text)
    _git(repo, "add", "-A")
    ts = f"{1_700_000_000 + n * 86400} +0000"
    env = {**os.environ, "GIT_AUTHOR_DATE": ts, "GIT_COMMITTER_DATE": ts,
           "GIT_AUTHOR_NAME": f"dev{n % 3}", "GIT_AUTHOR_EMAIL": f"dev{n % 3}@x.io",
           "GIT_COMMITTER_NAME": "c", "GIT_COMMITTER_EMAIL": "c@x.io"}
    _git(repo, "commit", "-q", "-m", msg, env=env)


@pytest.fixture(scope="module")
def synth_repo(tmp_path_factory):
    repo = str(tmp_path_factory.mktemp("szz_repo"))
    _git(repo, "init", "-q", "-b", "main")
    body = [f"line {i} = compute_value({i})" for i in range(200)]
    _commit(repo, "initial", 0, {"a.py": "\n".join(body) + "\n",
                                 "pkg/b.py": "x = 1\ny = 2\nz = 3\n"})
    n = 1
    for k in range(12):
        # feature commit edits scattered lines, then a fix edits some of them
        for j in range(k, 200, 17):
            body[j] = f"line {j} = compute_value({j}) + {k}"
        _commit(repo, f"add feature {k}", n, {"a.py": "\n".join(body) + "\n"}); n += 1
        for j in range(k, 200, 34):
            body[j] = f"line {j} = compute_value({j}) - {k}"
        if k % 3 == 0:                       # whitespace-only tweak (-w)
            body[k + 1] = "  " + body[k + 1]
        if k % 4 == 0:                       # move a block (-M)
            body = body[20:40] + body[:20] + body[40:]
        _commit(repo, f"fix bug in feature {k}", n,
                {"a.py": "\n".join(body) + "\n",
                 "pkg/b.py": f"x = {k}\ny = 2\nz = {k * 2}\n"}); n += 1
    # pure-addition fix (traces to nothing) and a delete-at-end fix
    body.append("extra = 1")
    _commit(repo, "fix: add missing guard", n, {"a.py": "\n".join(body) + "\n"}); n += 1
    body = body[:-5]
    _commit(repo, "bugfix trailing lines", n, {"a.py": "\n".join(body) + "\n"})
    return repo


def _settings():
    st = config.Settings()
    st.workers = 4
    return st


def _assert_equivalent(repo, tmp_path):
    gitio.configure_blame()
    ref = reference_szz_index(repo)
    cache = str(tmp_path / "x.szz.json")
    new = labels.build_szz_index(repo, _settings(), cache)
    assert list(new.keys()) == list(ref.keys())
    assert new == ref
    # the written cache reloads to the same index, and the checkpoint is gone
    assert labels.build_szz_index(repo, _settings(), cache) == ref
    assert not os.path.exists(cache + ".partial.jsonl")
    return ref


def test_equivalent_on_synthetic_repo(synth_repo, tmp_path):
    ref = _assert_equivalent(synth_repo, tmp_path)
    assert ref, "synthetic repo should yield bug-introducing commits"


def test_resume_from_partial_checkpoint(synth_repo, tmp_path):
    gitio.configure_blame()
    ref = reference_szz_index(synth_repo)
    cache = str(tmp_path / "r.szz.json")
    labels.build_szz_index(synth_repo, _settings(), cache)
    os.remove(cache)
    # rebuild a checkpoint holding only some fix commits, plus a torn line
    fixes = [c.sha for c, _ in gitio.iter_commits_with_files(synth_repo, "HEAD")
             if c.parents and config.is_fix_text(c.subject)]
    with open(cache + ".partial.jsonl", "w") as fh:
        for sha in fixes[: len(fixes) // 2]:
            _sha, bugs = labels._szz_fix_bugs(
                synth_repo, sha, gitio._run(synth_repo, "rev-parse", sha + "^").strip(),
                [p for _, _, p in gitio.numstat(synth_repo, sha)])
            fh.write(json.dumps({"sha": sha, "bugs": bugs}) + "\n")
        fh.write('{"sha": "torn')
    assert labels.build_szz_index(synth_repo, _settings(), cache) == ref


def test_line_ranges_merge():
    assert gitio._line_ranges([5, 1, 2, 3, 9, 30, 31]) == [(1, 5), (9, 9), (30, 31)]
    assert gitio._line_ranges([]) == []
    assert gitio._line_ranges([30, 10], pad=5) == [(5, 15), (25, 35)]
    assert gitio._line_ranges([3, 12], pad=5) == [(1, 17)]


@pytest.mark.skipif(not os.getenv("SZZ_EQUIV_REPO"), reason="set SZZ_EQUIV_REPO")
def test_equivalent_on_real_repo(tmp_path):
    _assert_equivalent(os.environ["SZZ_EQUIV_REPO"], tmp_path)
