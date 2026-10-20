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
    os.remove(cache + labels.BLAME_STORE_SUFFIX)
    with open(cache + labels.BLAME_STORE_SUFFIX, "w") as fh:
        fh.write(json.dumps({"fingerprint": dict(labels.szz_fingerprint(synth_repo, None),
                                                 checkpoint="per-path")}) + "\n")
        for sha in fixes[: len(fixes) // 2]:
            _sha, by_path, _calls, _fails = labels._szz_fix_bugs(
                synth_repo, sha, gitio._run(synth_repo, "rev-parse", sha + "^").strip(),
                [p for _, _, p in gitio.numstat(synth_repo, sha)])
            fh.write(json.dumps({"sha": sha, "paths": by_path}) + "\n")
        fh.write('{"sha": "torn')
    assert labels.build_szz_index(synth_repo, _settings(), cache) == ref


def test_blame_store_survives_a_new_window_or_selection(synth_repo, tmp_path, monkeypatch):
    """A finished run's blame store is reused by a run whose window or relevant
    set differs: no blame repeats, and the index is still exactly right."""
    gitio.configure_blame()
    ref = reference_szz_index(synth_repo)
    cache = str(tmp_path / "r.szz.json")
    assert labels.build_szz_index(synth_repo, _settings(), cache) == ref
    assert os.path.isfile(cache + labels.BLAME_STORE_SUFFIX)
    calls = []
    real = labels._szz_fix_bugs
    monkeypatch.setattr(labels, "_szz_fix_bugs", lambda *a: calls.append(a) or real(*a))
    os.remove(cache)                              # the index cache no longer fits
    assert labels.build_szz_index(synth_repo, _settings(), cache) == ref
    assert calls == []                            # every pair came from the store


def test_line_ranges_merge():
    assert gitio._line_ranges([5, 1, 2, 3, 9, 30, 31]) == [(1, 5), (9, 9), (30, 31)]
    assert gitio._line_ranges([]) == []
    assert gitio._line_ranges([30, 10], pad=5) == [(5, 15), (25, 35)]
    assert gitio._line_ranges([3, 12], pad=5) == [(1, 17)]


@pytest.mark.skipif(not os.getenv("SZZ_EQUIV_REPO"), reason="set SZZ_EQUIV_REPO")
def test_equivalent_on_real_repo(tmp_path):
    _assert_equivalent(os.environ["SZZ_EQUIV_REPO"], tmp_path)


def test_resolve_rename_forms():
    cases = {
        "src/{a => b}/x.rs": "src/b/x.rs",
        "a.rs => b.rs": "b.rs",
        "a/{b => }/c": "a/c",
        "a/{ => b}/c": "a/b/c",
        "plain.rs": "plain.rs",
        # literal braces outside the rename (rust-lang/rust's test suite)
        "tests/ui/{foo}.rs => tests/ui/bar.rs": "tests/ui/bar.rs",
        "x/{} => y": "y",
    }
    for raw, want in cases.items():
        assert gitio._resolve_rename(raw) == want, raw


def test_blame_with_relative_ignore_revs_file(tmp_path, monkeypatch):
    """A repo's .git-blame-ignore-revs given relative to our cwd must still
    work, although blame runs as `git -C <repo>` (it used to fail silently)."""
    root = tmp_path / "work"
    repo = root / "cloned_repos" / "r"
    repo.mkdir(parents=True)
    _git(str(repo), "init", "-q", "-b", "main")
    _commit(str(repo), "init", 0, {"f.txt": "a\nb\nc\n"})
    _commit(str(repo), "fix: b", 1, {"f.txt": "a\nB\nc\n"})
    first = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD~1"],
                           capture_output=True, text=True, check=True).stdout
    (repo / ".git-blame-ignore-revs").write_text(first)
    monkeypatch.chdir(root)
    rel_repo = os.path.join("cloned_repos", "r")
    try:
        gitio.configure_blame(ignore_revs_file=os.path.join(rel_repo, ".git-blame-ignore-revs"))
        assert len(gitio.blame_file(rel_repo, "HEAD", "f.txt")) == 3
        assert len(gitio.blame_lines(rel_repo, "HEAD", "f.txt", [2])) >= 1
    finally:
        gitio.configure_blame()


def test_blame_of_path_absent_at_commit_is_not_a_failure(synth_repo, capsys):
    """A PR that adds a file blames it at its base, where it doesn't exist.
    That's expected (no history), so it must not warn or count as a failure."""
    gitio.configure_blame()
    gitio._BLAME_STATS.update(calls=0, fails=0, absent=0, first_error=None)
    assert gitio.blame_file(synth_repo, "HEAD", "does/not/exist.py") == {}
    assert gitio.blame_lines(synth_repo, "HEAD", "does/not/exist.py", [1, 2]) == {}
    st = gitio.blame_stats()
    assert st["fails"] == 0 and st["absent"] == 2
    assert "[blame] warning" not in capsys.readouterr().err


def test_llm_reuse_skips_empty_answers_and_short_circuits_fetch(tmp_path):
    """Re-mine reuses saved LLM answers for the same commits; an answer with
    every flag null (the old call failed) is not reused, so it's asked again."""
    from features import llm_flags
    cols = list(llm_flags.FLAG_SCHEMA)
    good = {c: None for c in cols}; good[cols[0]] = True
    empty = {c: None for c in cols}
    p = tmp_path / "x.llm_reuse.json"
    p.write_text(json.dumps([{"base_sha": "b1", "head_sha": "h1", "flags": good},
                             {"base_sha": "b2", "head_sha": "h2", "flags": empty}]))
    try:
        assert llm_flags.load_reuse(str(p)) == 1
        st = config.Settings(); st.enable_llm = True; st.llm_backend = "claude_cli"
        got = llm_flags.fetch("/nonexistent", "b1", "h1", st)     # never touches git or the LLM
        assert got["status"] == "ok" and got["reused"] and got["raw"][cols[0]] is True
        assert llm_flags.resolve(got, st) == good
    finally:
        llm_flags._REUSE.clear()


def test_windowed_index_gives_identical_labels(synth_repo, tmp_path):
    """The window (szz_window) only drops fix commits szz_delay can't count, so
    every PR's days_to_szz_fix must match the full-history index exactly, while
    blaming fewer fix commits."""
    from datetime import datetime
    st = config.Settings()
    st.szz_maturity_days = 5
    commits = [c for c, _ in gitio.iter_commits_with_files(synth_repo, "HEAD") if c.parents]
    feats = [c for c in commits if c.subject.startswith("add feature")]
    picked = feats[3:7]                          # a window well inside the history
    prs = [{"merged_at": c.committed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "_range": (c.parents[0], c.sha)} for c in picked]
    full = labels.build_szz_index(synth_repo, st, None)
    window = labels.szz_window(prs, st)
    windowed = labels.build_szz_index(synth_repo, st, str(tmp_path / "w.json"), window=window)
    for pr in prs:
        base, head = pr["_range"]
        assert (labels.szz_delay(synth_repo, pr, base, head, full, st)
                == labels.szz_delay(synth_repo, pr, base, head, windowed, st))
    assert any(labels.szz_delay(synth_repo, pr, *pr["_range"], full, st) is not None for pr in prs)
    n_full = len({f for v in full.values() for f, _ in v})
    n_win = len({f for v in windowed.values() for f, _ in v})
    assert n_win < n_full
    # the cache knows its window: reused for a narrower one, rebuilt for a wider one
    assert labels.szz_cache_covers(str(tmp_path / "w.json"), window)
    assert not labels.szz_cache_covers(str(tmp_path / "w.json"), (None, None))


def test_relevant_pruning_follows_renames(tmp_path):
    """A bug line introduced in old.py, the file renamed to new.py, then fixed:
    pruning to the introducing commit must still blame new.py and find it,
    and agree with the full index on every relevant commit."""
    repo = str(tmp_path / "r")
    os.makedirs(repo)
    _git(repo, "init", "-q", "-b", "main")
    body = [f"value_{i} = {i} * 2 + offset" for i in range(40)]
    _commit(repo, "initial", 0, {"old.py": "\n".join(body) + "\n", "other.py": "x = 1\n"})
    body[10] = "value_10 = 10 * 3 + offset  # wrong factor"
    _commit(repo, "add feature", 1, {"old.py": "\n".join(body) + "\n"})
    intro = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True,
                           text=True).stdout.strip()
    _git(repo, "mv", "old.py", "new.py")
    _commit(repo, "move module", 2, {})
    _commit(repo, "touch other", 3, {"other.py": "x = 2\n"})
    body[10] = "value_10 = 10 * 2 + offset"
    _commit(repo, "fix wrong factor", 4, {"new.py": "\n".join(body) + "\n"})
    _commit(repo, "fix other", 5, {"other.py": "x = 3\n"})
    st = config.Settings()
    full = labels.build_szz_index(repo, st, None)
    pruned = labels.build_szz_index(repo, st, None, relevant={intro})
    assert intro in full and pruned.get(intro) == full[intro]
    assert "new.py" in labels.szz_relevant_paths(repo, {intro})
    assert "other.py" not in labels.szz_relevant_paths(repo, {intro})
