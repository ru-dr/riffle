"""The shared per-repo history (build_repo_index) answers every "as of base" query exactly as
the per-base walk (build_repo_index_full) does, on a repo with many commits, merges,
unmerged branches, out-of-order commit times and several as-of times."""
from __future__ import annotations

import os
import random
import subprocess
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import gitio  # noqa: E402
from features import history, repo_local  # noqa: E402

T0 = 1_600_000_000
FILES = [f"src/mod{i}/f{j}.py" for i in range(4) for j in range(4)] + ["tests/test_a.py", "docs/x.md",
                                                                       "gen/out.min.js", "README.md"]
AUTHORS = ["a@x.io", "b@x.io", "c@x.io", "dependabot[bot]@users.noreply.github.com"]
SUBJECTS = ["add thing", "fix crash in parser", "Revert \"add thing\"", "hotfix: urgent fix", "refactor", "docs", "feat: more"]


def _import(repo: str, seed: int = 5, n: int = 140) -> list[str]:
    """Build the repo with git fast-import: a main line, side branches merged back (some not),
    and commit times that are not monotone."""
    rnd = random.Random(seed)
    out: list[str] = []
    mark = [0]
    tips = {"main": None}
    t = T0

    def commit(branch, parents, files, who, subj, when):
        mark[0] += 1
        m = mark[0]
        out.append(f"commit refs/heads/{branch}\nmark :{m}")
        out.append(f"committer {who} <{who}> {when} +0000")
        out.append(f"data {len(subj)}\n{subj}")
        if parents:
            out.append(f"from :{parents[0]}")
            for p in parents[1:]:
                out.append(f"merge :{p}")
        for f in files:
            body = f"{m} {f} {rnd.random()}\n" * rnd.randint(1, 5)
            out.append(f"M 100644 inline {f}\ndata {len(body)}\n{body}")
        out.append("")
        return m

    main = commit("main", [], FILES[:6], AUTHORS[0], "init", t)
    branches: dict[str, int] = {}
    for i in range(n):
        t += rnd.randint(3600, 3 * 86400)
        who = rnd.choice(AUTHORS)
        subj = rnd.choice(SUBJECTS)
        files = rnd.sample(FILES, rnd.randint(1, 5))
        r = rnd.random()
        if r < 0.25:                       # side branch commit, later merged or abandoned
            b = f"side{rnd.randint(0, 5)}"
            parent = branches.get(b, main)
            branches[b] = commit(b, [parent], files, who, subj, t - rnd.randint(0, 5 * 86400))
        elif r < 0.4 and branches:         # merge a side branch into main
            b = rnd.choice(sorted(branches))
            main = commit("main", [main, branches.pop(b)], [], who, f"Merge {b}", t)
        else:
            main = commit("main", [main], files, who, subj, t)
    data = "\n".join(out) + "\n"
    subprocess.run(["git", "-C", repo, "fast-import", "--quiet"], input=data.encode(), check=True)
    return subprocess.run(["git", "-C", repo, "rev-list", "main"], capture_output=True, text=True,
                          check=True).stdout.split()


@pytest.fixture(scope="module")
def synth(tmp_path_factory):
    repo = str(tmp_path_factory.mktemp("synth"))
    subprocess.run(["git", "init", "-q", "-b", "main", repo], check=True)
    shas = _import(repo)
    return repo, shas


def _settings():
    st = config.Settings()
    return st


def _fake_ccn(monkeypatch):
    # a deterministic complexity per path with deliberate ties, so the hotspot ranking and its
    # tie order are exercised without lizard
    monkeypatch.setattr(history, "_blob_ccn", lambda repo, blob, path: float(1 + len(path) % 3))


def _assert_same(repo, base, st, ci_map, paths, authors, joined=()):
    new = history.build_repo_index(repo, base, st, ci_map)
    old = history.build_repo_index_full(repo, base, st, ci_map)
    assert new["base_time"] == old["base_time"]
    assert new["n_commits_total"] == old["n_commits_total"]
    assert new["repo_revert_base_rate"] == pytest.approx(old["repo_revert_base_rate"])
    assert new["repo_ci_total"] == old["repo_ci_total"] and new["repo_ci_fail"] == old["repo_ci_fail"]
    for p in paths:
        if p in joined:           # the shared index joins a renamed file's history, the walk does not
            continue
        nc = sorted(new["file_commits"].get(p, []))
        assert nc == sorted(old["file_commits"].get(p, [])), (base, p)
        assert new["file_authors"].get(p, set()) == old["file_authors"].get(p, set())
        for k in ("file_revert_ct", "file_fix_ct", "file_hotfix_365d", "file_test_cochange",
                  "file_ci_fail", "file_ci_total", "file_last_revert", "file_last_fix"):
            assert new[k].get(p, 0) == old[k].get(p, 0), (base, p, k)
        for k in ("file_decay_fix", "file_decay_revert"):
            assert new[k].get(p, 0.0) == pytest.approx(old[k].get(p, 0.0), abs=1e-12)
        assert dict(new["cochange"].get(p) or {}) == dict(old["cochange"].get(p) or {})
    for a in authors:
        for k in ("author_total_authored", "author_revert", "author_ci_fail", "author_ci_total",
                  "author_last", "author_commits"):
            assert new[k].get(a, 0) == old[k].get(a, 0), (base, a, k)
        assert new["author_rexp"].get(a, 0.0) == pytest.approx(old["author_rexp"].get(a, 0.0), abs=1e-9)
        assert dict(new["author_subsys"].get(a, {})) == dict(old["author_subsys"].get(a, {}))
    for name in ("size_pop", "nf_pop", "entropy_pop"):
        assert len(new[name]) == len(old[name]), (base, name)
        for x in (0, 1, 2, 5, 8, 20, 400, 0.3, 0.9):
            assert history._pctile_rank(new[name], x) == history._pctile_rank(old[name], x), (base, name, x)
    assert new["hotspot_scores"] == old["hotspot_scores"]
    assert new["hotspot_rank"] == old["hotspot_rank"]
    assert new["hotspot_topk"] == old["hotspot_topk"]


def test_shared_index_equals_walk(synth, monkeypatch):
    repo, shas = synth
    _fake_ccn(monkeypatch)
    st = _settings()
    rnd = random.Random(1)
    all_shas = subprocess.run(["git", "-C", repo, "rev-list", "--all"], capture_output=True, text=True,
                              check=True).stdout.split()
    ci_map = {s: rnd.choice(["failure", "success"]) for s in rnd.sample(all_shas, len(all_shas) // 3)}
    history.clear_repo_history()
    bases = rnd.sample(shas, 12) + [shas[0], shas[-1]]
    paths = FILES + ["nope.py"]
    authors = AUTHORS + ["ghost@x.io"]
    for base in bases:
        _assert_same(repo, base, st, ci_map, paths, authors)


def test_merge_commits_are_not_authored_work(synth, monkeypatch):
    repo, shas = synth
    _fake_ccn(monkeypatch)
    idx = history.build_repo_index_full(repo, shas[0], _settings())
    non_merge = [c for c, _ in gitio.iter_commits_with_files(repo, shas[0]) if len(c.parents) < 2]
    assert idx["n_commits_total"] == len(non_merge)
    assert sum(idx["author_total_authored"].values()) == len(non_merge)


def test_path_size_pctile_shared_equals_scan(synth):
    repo, shas = synth
    st = _settings()
    st.path_size_min_prior_changes = 1
    rnd = random.Random(3)
    numstats = [[(rnd.randint(0, 30), rnd.randint(0, 30), p) for p in rnd.sample(FILES, rnd.randint(1, 4))]
                for _ in range(10)]
    for base in rnd.sample(shas, 6):
        for ns in numstats:
            assert repo_local.path_size_pctile(repo, base, ns, st) == \
                repo_local.path_size_pctile_scan(repo, base, ns, st)


def _git(repo, *args, when="2024-01-01T00:00:00Z"):
    env = {**os.environ, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when, "GIT_AUTHOR_NAME": "t",
           "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    return subprocess.run(["git", "-C", repo, *args], check=True, env=env, capture_output=True, text=True).stdout


def test_history_follows_a_past_rename(tmp_path, monkeypatch):
    repo = str(tmp_path)
    _git(repo, "init", "-q", "-b", "main")
    body = "".join(f"line {i}\n" for i in range(40))
    for i, day in enumerate(("01", "02", "03")):
        with open(os.path.join(repo, "old.py"), "w") as fh:
            fh.write(body + f"edit {i}\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "fix: tweak" if i else "add", when=f"2024-01-{day}T00:00:00Z")
    _git(repo, "mv", "old.py", "new.py")
    _git(repo, "commit", "-qm", "rename", when="2024-01-04T00:00:00Z")
    with open(os.path.join(repo, "new.py"), "w") as fh:
        fh.write(body + "edit 9\n")
    _git(repo, "commit", "-qam", "later", when="2024-01-05T00:00:00Z")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "base", when="2024-01-06T00:00:00Z")
    base = _git(repo, "rev-parse", "HEAD").strip()
    history.clear_repo_history()
    st = _settings()
    idx = history.build_repo_index(repo, base, st)
    assert len(idx["file_commits"]["new.py"]) == 5          # add, 2 fixes, the rename, a later edit
    pf = history.per_file_features(repo, base, "new.py", idx, st)
    assert pf["NUC"] == 5 and pf["file_age_days"] == 5
    assert pf["file_fix_density"] == pytest.approx(2 / 5)


def _g(repo, *args, when):
    env = {**os.environ, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when, "GIT_AUTHOR_NAME": "t",
           "GIT_AUTHOR_EMAIL": "t@x.io", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x.io"}
    subprocess.run(["git", "-C", repo, *args], check=True, env=env, capture_output=True)


def test_shared_index_equals_walk_with_renames_and_merges(tmp_path, monkeypatch):
    """Files renamed (and edited after) on both main and a side branch, merged with real merge commits."""
    repo = str(tmp_path)
    subprocess.run(["git", "init", "-q", "-b", "main", repo], check=True)
    _fake_ccn(monkeypatch)
    day = [0]

    def put(name, text):
        os.makedirs(os.path.dirname(os.path.join(repo, name)) or repo, exist_ok=True)
        with open(os.path.join(repo, name), "w") as fh:
            fh.write(text)

    def commit(msg, author="a"):
        day[0] += 1
        _g(repo, "add", "-A", when=f"2024-01-{day[0]:02d}T00:00:00Z")
        _g(repo, "-c", f"user.name={author}", "-c", f"user.email={author}@x.io", "commit", "-qm", msg,
           when=f"2024-01-{day[0]:02d}T00:00:00Z")

    os.makedirs(os.path.join(repo, "lib"))
    body = "".join(f"line {i}\n" for i in range(40))
    put("src/a.py", body)
    put("src/b.py", body.replace("line 1\n", "other\n"))
    put("tests/test_a.py", "t\n")
    commit("init")
    _g(repo, "checkout", "-qb", "side", when="2024-01-05T00:00:00Z")
    put("src/b.py", body.replace("line 1\n", "other\n") + "side\n")
    commit("fix crash in b", "b")
    _g(repo, "mv", "src/b.py", "src/b_renamed.py", when="2024-01-06T00:00:00Z")
    commit("rename b", "b")
    _g(repo, "checkout", "-q", "main", when="2024-01-07T00:00:00Z")
    _g(repo, "mv", "src/a.py", "lib/a.py", when="2024-01-07T00:00:00Z")
    commit("move a", "a")
    put("lib/a.py", body + "more\n")
    put("tests/test_a.py", "t2\n")
    commit("fix: edit a after the move", "a")
    day[0] += 1
    _g(repo, "-c", "user.name=a", "-c", "user.email=a@x.io", "merge", "--no-ff", "-qm", "Merge side", "side",
       when=f"2024-01-{day[0]:02d}T00:00:00Z")
    _g(repo, "mv", "src/b_renamed.py", "lib/b.py", when="2024-01-20T00:00:00Z")
    commit("move b again", "c")
    put("lib/b.py", body + "tail\n")
    commit("Revert \"fix crash in b\"", "c")
    shas = subprocess.run(["git", "-C", repo, "rev-list", "main"], capture_output=True, text=True,
                          check=True).stdout.split()
    st = _settings()
    paths = ["src/a.py", "lib/a.py", "src/b.py", "src/b_renamed.py", "lib/b.py", "tests/test_a.py", "nope.py"]
    renamed = {"lib/a.py", "src/b_renamed.py", "lib/b.py"}
    for base in shas:
        history.clear_repo_history()
        _assert_same(repo, base, st, {shas[1]: "failure"}, paths, ["a@x.io", "b@x.io", "c@x.io"],
                     joined=renamed)
        new = history.build_repo_index(repo, base, st, {shas[1]: "failure"})
        old = history.build_repo_index_full(repo, base, st, {shas[1]: "failure"})
        for p in renamed:         # joined across the rename: everything the walk saw, and more
            assert set(old["file_commits"].get(p, [])) <= set(new["file_commits"].get(p, [])), (base, p)
    # the rename is followed: the file's own history reaches back before the move
    history.clear_repo_history()
    new = history.build_repo_index(repo, shas[0], st)
    assert {c[2] for c in new["file_commits"]["lib/b.py"]} >= {"init", "fix crash in b", "rename b"}
    assert {c[2] for c in new["file_commits"]["lib/a.py"]} >= {"init", "move a"}
