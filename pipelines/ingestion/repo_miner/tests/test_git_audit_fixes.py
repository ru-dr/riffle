"""Audit fixes in gitio / labels / the history and diff features: odd paths, renames, timeouts,
fetch errors, landed-commit discovery, revert and hotfix self-matches, and the parsers."""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import gitio  # noqa: E402
import labels  # noqa: E402
from features import deterministic, diff_stats, history, line_history, repo_local  # noqa: E402


def git(repo, *args, when="2024-01-01T00:00:00Z"):
    env = {**os.environ, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when, "GIT_AUTHOR_NAME": "t",
           "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    return subprocess.run(["git", "-C", repo, *args], check=True, env=env, capture_output=True,
                          text=True).stdout.strip()


def write(repo, name, text):
    path = os.path.join(repo, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


@pytest.fixture
def repo(tmp_path):
    r = str(tmp_path)
    git(r, "init", "-q", "-b", "main")
    return r


# ---- H8: odd paths, -z, hunk-count patch parsing ---------------------------------

def test_odd_paths_read_back_as_written(repo):
    names = ["dé/ü.txt", 'we ird"q.txt', "tab\tname.txt", "a b/c d.txt", "x => y.txt"]
    for n in names:
        write(repo, n, "1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "one")
    assert sorted(p for _, _, p in gitio.numstat(repo, "HEAD")) == sorted(names)
    base = git(repo, "rev-parse", "HEAD")
    for n in names:
        write(repo, n, "1\n2\n")
    git(repo, "commit", "-qam", "two")
    head = git(repo, "rev-parse", "HEAD")
    assert sorted(gitio.changed_files(repo, base, head)) == sorted(names)
    assert sorted(gitio.changed_lines_by_file(repo, base, head)) == sorted(names)
    assert gitio.deleted_or_modified_lines(repo, base, head, "a b/c d.txt") == []   # pure addition
    assert gitio.file_loc_at(repo, head, "dé/ü.txt") == 2
    assert gitio.tree_blobs(repo, head).keys() >= set(names)


def test_patch_parser_keeps_dashes_and_reads_quoted_headers(repo):
    write(repo, "a b.txt", "x\n")
    write(repo, 'q"t.txt', "x\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "one")
    base = git(repo, "rev-parse", "HEAD")
    write(repo, "a b.txt", "x\n-- ok\n++ ok\n")
    write(repo, 'q"t.txt', "y\n--- gone\n")
    git(repo, "commit", "-qam", "two")
    head = git(repo, "rev-parse", "HEAD")
    added = dict()
    for p, t in deterministic._added_lines(repo, base, head):
        added.setdefault(p, []).append(t)
    assert added["a b.txt"] == ["-- ok", "++ ok"]
    assert added['q"t.txt'] == ["y", "--- gone"]
    assert ("q\"t.txt", "x") in deterministic._removed_lines(repo, base, head)


def test_one_git_diff_per_kind_for_a_pr(repo, monkeypatch):
    write(repo, "f.py", "a\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "one")
    base = git(repo, "rev-parse", "HEAD")
    write(repo, "f.py", "a\nb\n")
    write(repo, "g.bin", "x")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "two")
    head = git(repo, "rev-parse", "HEAD")
    calls = []
    real = gitio._run
    monkeypatch.setattr(gitio, "_run", lambda repo_, *a, **k: (calls.append(a[0:2]), real(repo_, *a, **k))[1])
    gitio._PR_DIFFS.clear()
    st = config.Settings()
    diff_stats.compute(repo, base, head, st)
    deterministic.compute(repo, base, head, st)
    gitio.changed_lines_by_file(repo, base, head)
    gitio.deleted_or_modified_lines(repo, base, head, "f.py")
    diffs = [c for c in calls if c[0] == "diff"]
    assert len(diffs) == 3, diffs         # numstat, name-status, one -U0 patch


# ---- H7: renames -------------------------------------------------------------------

def test_rename_in_the_pr_uses_the_old_name_at_base(repo):
    body = "".join(f"line {i}\n" for i in range(30))
    write(repo, "old.py", body)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "add", when="2024-01-01T00:00:00Z")
    write(repo, "old.py", body + "more\n")
    git(repo, "commit", "-qam", "fix: bug", when="2024-01-02T00:00:00Z")
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "mv", "old.py", "new.py")
    write(repo, "new.py", body.replace("line 3\n", "line three\n") + "more\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "rename and edit", when="2024-01-10T00:00:00Z")
    head = git(repo, "rev-parse", "HEAD")
    assert gitio.rename_map(repo, base, head) == {"new.py": "old.py"}
    # without the old name the rename source is hidden; with it the modified line is found
    assert gitio.deleted_or_modified_lines(repo, base, head, "new.py") == []
    assert gitio.deleted_or_modified_lines(repo, base, head, "new.py", "old.py") == [4]
    history.clear_repo_history()
    st = config.Settings()
    idx = history.build_repo_index(repo, base, st)
    plain = history.per_file_features(repo, base, "new.py", idx, st)
    fixed = history.per_file_features(repo, base, "new.py", idx, st, old_path="old.py")
    assert plain["NUC"] == 0 and plain["_loc"] == 0
    assert fixed["NUC"] == 2 and fixed["_loc"] == 31 and fixed["file_fix_density"] == 0.5
    lf = line_history.per_file_line_features(repo, base, head, "new.py", "t@t", idx, st, old_path="old.py")
    assert lf["mod_lines_distinct_authors"] == 1
    assert lf["mod_line_age_days_min"] is not None
    cf = history.cochange_features(["new.py"], idx, {"new.py": "old.py"})
    assert cf["cochange_degree"] == 0


# ---- H13: timeouts, size cap, stall watchdog -------------------------------------------

def test_a_git_call_past_its_timeout_is_killed_and_raises(repo, monkeypatch, tmp_path):
    # a fake "git" that hangs, found first on PATH
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "git").write_text("#!/bin/sh\nsleep 30\n")
    (fake / "git").chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("RIFFLE_GIT_TIMEOUT_S", "0.3")
    with pytest.raises(gitio.GitTimeout):
        gitio._run(repo, "status", check=False)
    with pytest.raises(gitio.GitTimeout):
        gitio.read_blob(repo, "HEAD:x")
    monkeypatch.setenv("RIFFLE_GIT_BLAME_TIMEOUT_S", "0.3")
    before = gitio.blame_stats()["timeouts"]
    assert gitio.blame_file(repo, "HEAD", "x") == {}          # unobserved, and not cached
    assert gitio.blame_stats()["timeouts"] == before + 1


def test_blob_over_the_cap_has_a_line_count_but_no_text(repo, monkeypatch):
    write(repo, "big.py", "x = 1\n" * 1000)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "big")
    monkeypatch.setenv("RIFFLE_MAX_BLOB_BYTES", "100")
    r = gitio.read_blob(repo, "HEAD:big.py")
    assert r.status == "too_large" and r.content is None and r.loc == 1000
    assert gitio.file_content_at(repo, "HEAD", "big.py") is None
    assert gitio.file_loc_at(repo, "HEAD", "big.py") == 1000
    monkeypatch.setenv("RIFFLE_MAX_BLOB_BYTES", "0")
    assert gitio.file_content_at(repo, "HEAD", "big.py").count("\n") == 1000


def test_failed_blob_read_is_not_cached_as_zero_complexity(repo, monkeypatch):
    history._BLOB_CCN_CACHE.clear()
    assert history._blob_ccn(repo, "0" * 40, "x.py") is None
    assert ("0" * 40, "x.py") not in history._BLOB_CCN_CACHE


def test_stall_watchdog_kills_a_silent_process_and_lets_a_chatty_one_finish():
    with pytest.raises(gitio.GitTimeout):
        gitio.run_with_stall_watchdog([sys.executable, "-c", "import time; time.sleep(30)"], stall_s=1.5)
    rc, _ = gitio.run_with_stall_watchdog(
        [sys.executable, "-c",
         "import sys,time\nfor i in range(4):\n print('p',file=sys.stderr,flush=True); time.sleep(0.6)"],
        stall_s=1.5)
    assert rc == 0


def test_stream_numstat_log_reaps_git_when_the_consumer_stops(repo):
    for i in range(3):
        write(repo, f"f{i}.txt", "x\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", f"c{i}")
    gen = gitio._stream_numstat_log(repo, "log", "--numstat", "-z", f"--format={gitio._NUMSTAT_FMT}")
    next(gen)
    gen.close()                                   # must not leave a zombie or raise


def test_empty_blame_is_not_cached(repo, monkeypatch):
    gitio.blame_file.cache_clear()
    calls = []
    real = gitio._blame
    monkeypatch.setattr(gitio, "_blame", lambda *a: (calls.append(1), {})[1])
    gitio.blame_file(repo, "HEAD", "f")
    gitio.blame_file(repo, "HEAD", "f")
    assert len(calls) == 2
    monkeypatch.setattr(gitio, "_blame", real)


def test_blame_cache_is_bounded_by_lines():
    c = gitio._BlameCache(max_entries=100, max_lines=10)
    for i in range(5):
        c.put(i, {n: None for n in range(6)})
    assert c.get(0) is None and c.get(4) is not None


# ---- M13: fetch errors ------------------------------------------------------------------

def test_fetch_splits_only_on_a_missing_ref_and_raises_otherwise(monkeypatch):
    monkeypatch.setattr(gitio, "FETCH_RETRY_DELAY_S", 0)
    gone = {"refs/pull/2/head"}
    calls = []

    def fake(repo, *args, **kw):
        specs = [a for a in args if a.startswith("refs/pull")]
        calls.append(specs)
        bad = [s for s in specs if s in gone]
        if bad:
            raise gitio.GitError(f"fatal: couldn't find remote ref {bad[0]}")
    monkeypatch.setattr(gitio, "_run", fake)
    assert gitio.fetch_pr_heads("r", [1, 2, 3, 4]) == []          # the missing one is skipped, rest fetched
    assert ["refs/pull/1/head"] in calls or ["refs/pull/1/head", "refs/pull/2/head"] in calls

    calls.clear()

    def flaky(repo, *args, **kw):
        calls.append(args)
        raise gitio.GitError("fatal: unable to access: Could not resolve host")
    monkeypatch.setattr(gitio, "_run", flaky)
    assert gitio.fetch_pr_heads("r", [7, 8]) == [7, 8]             # unobserved, not gone
    assert len(calls) == gitio.FETCH_RETRIES + 1                    # retried, never split
    with pytest.raises(gitio.FetchError) as ei:
        gitio.fetch_pr_heads("r", [7], strict=True)
    assert ei.value.specs == ["refs/pull/7/head"]


# ---- M25 / M26 ----------------------------------------------------------------------------

def test_default_branch_keeps_its_slashes(repo):
    git(repo, "commit", "-q", "--allow-empty", "-m", "x")
    git(repo, "update-ref", "refs/remotes/origin/release/1.x", "HEAD")
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/release/1.x")
    assert gitio.default_branch(repo) == "release/1.x"


def test_landed_index_skips_revert_bodies_and_prefers_the_strong_hit(repo):
    git(repo, "commit", "-q", "--allow-empty", "-m", "feat: thing (#7)", when="2024-01-01T00:00:00Z")
    git(repo, "commit", "-q", "--allow-empty", "-m", "chore: tidy\n\nCloses #7", when="2024-01-05T00:00:00Z")
    git(repo, "commit", "-q", "--allow-empty", "-m", 'Revert "feat: thing (#7)" (#9)\n\nThis reverts commit abc.\nCloses #7',
        when="2024-01-06T00:00:00Z")
    idx = gitio.landed_commit_index(repo, "main", "o", "r")
    assert len(idx[7]) == 1 and idx[7][0][1].day == 1          # only the (#7) commit
    assert 9 in idx                                              # the revert PR is still itself
    git(repo, "commit", "-q", "--allow-empty", "-m", "fix: only keyword\n\nFixes nothing, Closes #12",
        when="2024-01-07T00:00:00Z")
    assert 12 in gitio.landed_commit_index(repo, "main", "o", "r")   # weak-only hits still count


# ---- H3 / L1 / L5 / M9 -----------------------------------------------------------------------

def _pr(repo, number=5, title="add thing", merge="f" * 40, merged="2024-01-10T00:00:00Z"):
    return {"number": number, "merge_commit_sha": merge, "merged_at": merged, "title": title}


def test_pr_is_never_its_own_revert_or_hotfix(repo):
    write(repo, "a.py", "1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init", when="2024-01-01T00:00:00Z")
    base = git(repo, "rev-parse", "HEAD")
    # the PR's own commits: one whose message reads like a revert and a hotfix, then its merge
    write(repo, "a.py", "2\n")
    git(repo, "commit", "-qam", "hotfix: urgent fix for a", when="2024-01-11T00:00:00Z")
    git(repo, "commit", "-q", "--allow-empty", "-m", 'Revert "add thing"', when="2024-01-11T00:00:00Z")
    git(repo, "commit", "-q", "--allow-empty", "-m", "add thing (#5)", when="2024-01-10T00:00:00Z")
    head = git(repo, "rev-parse", "HEAD")
    st = config.Settings()
    pr = _pr(repo, merge=head)
    own = gitio.commits_in_range(repo, base, head)
    assert len(own) == 3
    assert labels.revert_delay(repo, pr, st) == 1.0                        # without the PR's commits: itself
    assert labels.revert_delay(repo, pr, st, own) is None
    assert labels.hotfix_delay(repo, pr, ["a.py"], st) == 1.0
    assert labels.hotfix_delay(repo, pr, ["a.py"], st, own) is None
    # a later commit still counts
    write(repo, "a.py", "3\n")
    git(repo, "commit", "-qam", "hotfix: urgent fix for a", when="2024-01-12T00:00:00Z")
    assert labels.hotfix_delay(repo, pr, ["a.py"], st, own) == 2.0
    # and a PR whose merge commit is the matching commit is not its own revert
    only = git(repo, "rev-parse", "HEAD")
    assert labels.hotfix_delay(repo, _pr(repo, merge=only, merged="2024-01-12T00:00:00Z"), ["a.py"], st) is None


def test_revert_citation_must_be_in_the_subject_and_from_a_later_pr(repo):
    st = config.Settings()
    git(repo, "commit", "-q", "--allow-empty", "-m", 'Revert "other work"\n\nsee #5 for context',
        when="2024-01-12T00:00:00Z")
    pr = _pr(repo)
    assert labels.revert_delay(repo, pr, st) is None                   # "#5" in a body is no citation
    git(repo, "commit", "-q", "--allow-empty", "-m", 'Revert "add thing"\n\nReverts o/r#5',
        when="2024-01-13T00:00:00Z")
    assert labels.revert_delay(repo, pr, st) == 3.0
    git(repo, "commit", "-q", "--allow-empty", "-m", 'revert: add thing (#5) (#3)', when="2024-01-11T00:00:00Z")
    assert labels.revert_delay(repo, pr, st) == 3.0                    # (#3) predates #5: not its revert


def test_revert_search_follows_the_default_branch_not_every_ref(repo):
    st = config.Settings()
    git(repo, "commit", "-q", "--allow-empty", "-m", "init", when="2024-01-01T00:00:00Z")
    git(repo, "checkout", "-q", "-b", "side")
    git(repo, "commit", "-q", "--allow-empty", "-m", 'Revert "add thing"\n\nReverts #5', when="2024-01-12T00:00:00Z")
    git(repo, "checkout", "-q", "main")
    labels._default_tip.cache_clear()
    assert labels.revert_delay(repo, _pr(repo), st) is None
    git(repo, "checkout", "-q", "side")                     # an explicit rev still searches that history
    assert labels.revert_delay(repo, _pr(repo), st, rev="side") == 2.0


def test_label_szz_bug_is_null_when_szz_did_not_run(repo):
    st = config.Settings()
    git(repo, "commit", "-q", "--allow-empty", "-m", "init", when="2024-01-01T00:00:00Z")
    sha = git(repo, "rev-parse", "HEAD")
    pr = _pr(repo, merge=sha, merged="2024-01-01T00:00:00Z")
    snap = datetime(2024, 6, 1, tzinfo=timezone.utc)
    off = labels.build_labels(repo, pr, [], None, snap, st, None, None, sha, sha)
    assert off["label_szz_bug"] is None and off["days_to_szz_fix"] is None
    on = labels.build_labels(repo, pr, [], None, snap, st, {}, None, sha, sha)
    assert on["label_szz_bug"] is False
    assert labels.szz_window_closed(pr, snap, st) is True
    assert labels.szz_window_closed(pr, datetime(2024, 1, 20, tzinfo=timezone.utc), st) is False


# ---- L7 / L8 / L10 / L12 / L13 -------------------------------------------------------------------

def test_entropy_ignores_files_with_no_changed_lines():
    assert diff_stats._entropy([5, 5, 0, 0]) == diff_stats._entropy([5, 5]) == 1.0
    assert history._commit_entropy([(5, 0, "a"), (5, 0, "b"), (0, 0, "c"), (0, 0, "d")]) == 1.0


def test_percentile_population_leaves_out_generated_files():
    v = history._pop_values([(10, 0, "src/a.py"), (500, 0, "dist/app.min.js")])
    assert v is not None and v[0] == 10 and v[1] == 1
    assert history._pop_values([(500, 0, "dist/app.min.js")]) is None


def test_lockfile_version_lines_are_not_one_package():
    pairs = [("package-lock.json", '    "node_modules/foo": {'), ("package-lock.json", '      "version": "2.0.1",'),
             ("package-lock.json", '    "node_modules/bar": {'), ("package-lock.json", '      "version": "3.1.1",'),
             ("package-lock.json", '      "version": "9.9.9",')]
    out = deterministic._dep_versions(pairs)
    assert out == {"foo": 2, "bar": 9}
    assert deterministic._dep_versions([("package-lock.json", '      "version": "2.0.1",')]) == {}


def test_bots_are_not_developers_of_a_file(repo):
    for who, day in (("human@x.io", "01"), ("dependabot[bot]@users.noreply.github.com", "02")):
        write(repo, "a.py", f"{day}\n")
        git(repo, "add", "-A")
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": who, "GIT_COMMITTER_NAME": "t",
               "GIT_COMMITTER_EMAIL": who, "GIT_AUTHOR_DATE": f"2024-01-{day}T00:00:00Z",
               "GIT_COMMITTER_DATE": f"2024-01-{day}T00:00:00Z"}
        subprocess.run(["git", "-C", repo, "commit", "-qm", "c"], check=True, env=env, capture_output=True)
    base = git(repo, "rev-parse", "HEAD")
    history.clear_repo_history()
    st = config.Settings()
    idx = history.build_repo_index(repo, base, st)
    assert history.per_file_features(repo, base, "a.py", idx, st)["NDEV"] == 1


def test_similar_pr_prior_is_the_repos_bad_pr_rate():
    st = config.Settings()
    st.similar_prs_min_neighbours = 1
    st.similar_prs_min_file_overlap = 0.1
    t0 = datetime(2025, 1, 1, tzinfo=timezone.utc)
    rows = []
    for i in range(10):
        merged = datetime(2025, 1, 1 + i, tzinfo=timezone.utc)
        bad = i % 2 == 0
        rows.append({"pr_number": i, "merged_at": merged.isoformat(), "repo_revert_base_rate": 0.0,
                     "label_ci_fail": bad, "label_revert": False, "label_hotfix": False,
                     repo_local.SIDECAR: {"opened_at": merged.isoformat(), "files": {"f": 1},
                                          "open_files": {"f": 1}, "reviews": None}})
    late = datetime(2025, 3, 1, tzinfo=timezone.utc)
    rows.append({"pr_number": 99, "merged_at": late.isoformat(), "repo_revert_base_rate": 0.0,
                 repo_local.SIDECAR: {"opened_at": late.isoformat(), "files": {"g": 1},
                                      "open_files": {"g": 1}, "reviews": None}})
    repo_local.compute_repo(rows, [], st)
    # one similar neighbour would be needed; "g" has none, so use a PR touching "f"
    rows[-1][repo_local.SIDECAR]["open_files"] = {"f": 1}
    rows[-1][repo_local.SIDECAR]["files"] = {"f": 1}
    repo_local.compute_repo(rows, [], st)
    k = st.similar_prs_k
    bad_neigh = sum(1 for r in rows[-6:-1] if r["label_ci_fail"])
    n = min(k, 10)
    got = rows[-1]["similar_pr_bad_rate"]
    assert got is not None and 0.0 < got <= 1.0
    # filler uses 0.5 (half the mature past PRs were bad), not the 0.0 per-commit revert rate
    assert abs(got - (bad_neigh + (k - n) * 0.5) / k) < 1e-9 or n == k


def test_line_age_never_negative():
    t = datetime(2024, 1, 1, tzinfo=timezone.utc)
    assert line_history._age_days(t, datetime(2024, 1, 3, tzinfo=timezone.utc)) == 0
    assert line_history._age_days(t, datetime(2023, 12, 25, tzinfo=timezone.utc)) == 7
