"""The miner's checkpoint, resume and final write (audit H1, H2, M4-M7, L19, L20, L36, L37).

Everything here is offline: synthetic rows in temp dirs, and one throwaway git repo mined with
--no-network semantics (git discovery, no API, no LLM)."""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

import pytest

MINER = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MINER))

import config  # noqa: E402
import mine_repo  # noqa: E402
import snapshot_capture as sc  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _settings(**kw):
    st = config.Settings()
    st.enable_llm = False
    st.enable_github_api = False
    st.enable_deps_cve = False
    st.enable_scanners = False
    st.enable_api_contract = False
    st.pr_discovery = "git"
    st.min_age_days = 0
    st.workers = 2
    for k, v in kw.items():
        setattr(st, k, v)
    return st


def _lines(path):
    return [json.loads(x) for x in pathlib.Path(path).read_text().splitlines() if x.strip()]


def _put(path, rows, tail=""):
    with open(path, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
        fh.write(tail)


# ---- reading ---------------------------------------------------------------

def test_reader_skips_torn_lines_dedupes_and_keeps_last(tmp_path):
    st = _settings()
    stamp = mine_repo._ckpt_stamp(st, NOW)
    p = tmp_path / "o.partial.jsonl"
    _put(p, [{"repo": "Org/Name", "pr_number": 1, "v": "old", mine_repo.CKPT_STAMP: stamp},
             {"repo": "other/x", "pr_number": 1},
             {"repo": "org/name", "pr_number": 1, "v": "new", mine_repo.CKPT_STAMP: stamp},
             {"repo": "org/name", "pr_number": 2}],            # no stamp: an older miner's row
         tail='{"repo": "org/name", "pr_num')                  # torn last line
    rows, stale, diff = mine_repo.load_checkpoint(str(p), "org/NAME", stamp)
    assert {r["pr_number"]: r.get("v") for r in rows} == {1: "new", 2: None}
    assert stale == 0 and diff == []
    assert all(mine_repo.CKPT_STAMP not in r for r in rows)


def test_rows_made_under_other_settings_are_stale(tmp_path):
    mine = mine_repo._ckpt_stamp(_settings(), NOW)
    other = mine_repo._ckpt_stamp(_settings(enable_llm=True), NOW)
    p = tmp_path / "o.partial.jsonl"
    _put(p, [{"repo": "a/b", "pr_number": 1, mine_repo.CKPT_STAMP: other},
             {"repo": "a/b", "pr_number": 2, mine_repo.CKPT_STAMP: mine}])
    rows, stale, diff = mine_repo.load_checkpoint(str(p), "a/b", mine)
    assert [r["pr_number"] for r in rows] == [2]
    assert stale == 1 and "llm" in diff


def test_stamp_follows_version_minor_and_obs_day_not_patch():
    st = _settings()
    a = mine_repo._ckpt_stamp(st, NOW)
    assert a["obs_cutoff"] == "2026-01-01"
    assert a["miner"].count(".") == 1
    assert mine_repo._ckpt_stamp(st, NOW.replace(hour=5)) == a
    assert mine_repo._ckpt_stamp(_settings(enable_szz=False), NOW) != a


def test_heal_tail_cuts_a_fragment_and_terminates_a_whole_record(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text('{"a": 1}\n{"a": 2}')
    sc.heal_tail(str(p))
    assert p.read_text() == '{"a": 1}\n{"a": 2}\n'
    p.write_text('{"a": 1}\n{"a"')
    sc.heal_tail(str(p))
    assert p.read_text() == '{"a": 1}\n'


# ---- H1: only this repo leaves the shared checkpoint -------------------------

def test_clean_write_removes_only_this_repos_lines(tmp_path):
    p = tmp_path / "o.partial.jsonl"
    _put(p, [{"repo": "A/one", "pr_number": 1}, {"repo": "b/two", "pr_number": 7},
             {"repo": "a/ONE", "pr_number": 2}], tail='{"repo": "b/two", "pr_')
    mine_repo.drop_repo_from_checkpoint(str(p), "a/one")
    assert _lines(p) == [{"repo": "b/two", "pr_number": 7}]
    mine_repo.drop_repo_from_checkpoint(str(p), "b/two")
    assert not p.exists()                                      # nothing left: the file goes


# ---- H2, M4, M5: the final write ---------------------------------------------

def test_append_with_no_rows_leaves_the_dataset_alone(tmp_path):
    out = tmp_path / "d.jsonl"
    _put(out, [{"repo": "a/b", "pr_number": 1}, {"repo": "a/b", "pr_number": 2}])
    before = out.read_text()
    assert mine_repo._write([], str(out), append=True) == (2, True)
    assert out.read_text() == before
    assert mine_repo._write([], str(tmp_path / "none.jsonl"), append=True) == (0, True)
    assert not (tmp_path / "none.jsonl").exists()


def test_append_with_no_rows_leaves_parquet_alone(tmp_path):
    pd = pytest.importorskip("pandas")
    pytest.importorskip("pyarrow")
    out = tmp_path / "d.parquet"
    pd.DataFrame([{"repo": "a/b", "pr_number": 1}]).to_parquet(out, index=False)
    before = out.read_bytes()
    assert mine_repo._write([], str(out), append=True) == (1, True)
    assert out.read_bytes() == before


def test_a_failed_write_keeps_the_old_file_and_leaves_no_temp(tmp_path, monkeypatch):
    out = tmp_path / "d.csv"
    out.write_text("repo,pr_number\na/b,1\n")

    def boom(rows, path):
        with open(path, "w") as fh:
            fh.write("half")
        raise OSError("disk full")
    monkeypatch.setattr(mine_repo, "_write_csv", boom)
    with pytest.raises(OSError):
        mine_repo._write([{"repo": "a/b", "pr_number": 2}], str(out))
    assert out.read_text() == "repo,pr_number\na/b,1\n"
    assert [x.name for x in tmp_path.iterdir()] == ["d.csv"]


def test_parquet_failure_keeps_earlier_repos_in_the_fallback(tmp_path, monkeypatch):
    pd = pytest.importorskip("pandas")
    pytest.importorskip("pyarrow")
    out = tmp_path / "d.parquet"
    pd.DataFrame([{"repo": "a/one", "pr_number": 1}]).to_parquet(out, index=False)
    alt = tmp_path / "d.jsonl"
    _put(alt, [{"repo": "c/three", "pr_number": 3}])           # an earlier fallback's rows

    def boom(self, *a, **k):
        raise ValueError("cannot convert")
    monkeypatch.setattr(pd.DataFrame, "to_parquet", boom)
    total, ok = mine_repo._write([{"repo": "b/two", "pr_number": 2}], str(out), append=True)
    assert ok and total == 3
    assert {(r["repo"], r["pr_number"]) for r in _lines(alt)} == {
        ("a/one", 1), ("b/two", 2), ("c/three", 3)}
    assert pd.read_parquet(out).shape[0] == 1                  # the parquet itself untouched


def test_append_merge_tolerates_a_torn_line_in_a_jsonl_dataset(tmp_path):
    out = tmp_path / "d.jsonl"
    _put(out, [{"repo": "a/b", "pr_number": 1}], tail='{"repo": "a/')
    total, ok = mine_repo._write([{"repo": "a/b", "pr_number": 2}], str(out), append=True)
    assert ok and total == 2


# ---- capture files (L19, L20) -------------------------------------------------

def test_writer_resume_drops_orphans_and_does_not_write_twice(tmp_path):
    d = tmp_path / "snap"
    w = sc.Writer(str(d), resume=False)
    for n in (1, 2, 3):
        w.write({"pr_number": n}, {"number": n})
    w.close()
    (d / sc.CAPTURE_FILE).write_text((d / sc.CAPTURE_FILE).read_text() + '{"pr_number": 4')   # torn
    w = sc.Writer(str(d), resume=True, keep={1, 2})            # PR 3 never reached the checkpoint
    assert w.have_capture == {1, 2} and w.have_index == {1, 2}
    w.write({"pr_number": 2}, {"number": 2})                   # already there: not repeated
    w.write({"pr_number": 3}, {"number": 3})                   # re-mined
    w.close()
    assert [r["pr_number"] for r in sc.read_jsonl(str(d / sc.CAPTURE_FILE))] == [1, 2, 3]
    assert [r["number"] for r in sc.read_jsonl(str(d / sc.INDEX_FILE))] == [1, 2, 3]


# ---- a real (offline) repo through mine_repo ----------------------------------

def _git(repo, *args, date=None, author="A <a@example.com>"):
    name, _, mail = author.partition(" <")
    env = dict(os.environ, GIT_AUTHOR_NAME=name, GIT_AUTHOR_EMAIL=mail.rstrip(">"),
               GIT_COMMITTER_NAME=name, GIT_COMMITTER_EMAIL=mail.rstrip(">"))
    if date:
        env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    return subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True,
                          text=True).stdout.strip()


@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    r = tmp_path_factory.mktemp("ckpt_repo")
    _git(r, "init", "-q", "-b", "main")
    (r / "a.py").write_text("def f():\n    return 1\n")
    _git(r, "add", ".")
    _git(r, "commit", "-qm", "init", date="2025-01-01T00:00:00Z")
    for i in range(1, 7):
        with open(r / "a.py", "a") as fh:
            fh.write(f"def g{i}():\n    return {i}\n")
        _git(r, "commit", "-qam", f"change {i} (#{i})", date=f"2025-0{i + 1}-01T00:00:00Z")
    return r


def _mine(repo, tmp_path, name="syn", **kw):
    st = _settings(snapshot_dir=str(tmp_path / "snap"), ci_cache_dir=str(tmp_path / "cache"), **kw)
    out = str(tmp_path / "rows.jsonl")
    return mine_repo.mine_repo(str(repo), "Example", name, st, out_path=out), out


def test_rows_are_keyed_owner_name_without_network_and_checkpoint_is_stamped(repo, tmp_path):
    rows, out = _mine(repo, tmp_path)
    assert len(rows) == 6
    assert {r["repo"] for r in rows} == {"Example/syn"}        # not the clone's directory name
    ck = _lines(out + ".partial.jsonl")
    assert len(ck) == 6 and all(mine_repo.CKPT_STAMP in c for c in ck)
    assert all(mine_repo.CKPT_STAMP not in r for r in rows)


def test_resume_keeps_compatible_rows_and_remines_stale_ones(repo, tmp_path):
    rows, out = _mine(repo, tmp_path)
    ck_path = out + ".partial.jsonl"
    ck = _lines(ck_path)
    fresh = ck[0]                                              # stamped, same settings: kept
    fresh["title_len"] = 4242
    legacy = dict(ck[1]); legacy.pop(mine_repo.CKPT_STAMP); legacy["title_len"] = 4242   # no stamp: kept
    stale = dict(ck[2]); stale[mine_repo.CKPT_STAMP] = dict(stale[mine_repo.CKPT_STAMP], llm=True)
    stale["title_len"] = 4242                                  # other settings: mined again
    dup = dict(ck[3]); dup["title_len"] = 4242                 # duplicate lines: the last wins
    older = dict(ck[3]); older["title_len"] = 1
    other_repo = {"repo": "someone/else", "pr_number": 99}
    _put(ck_path, [other_repo, older, fresh, legacy, stale, dup], tail='{"repo": "Example/sy')
    rows2, _ = _mine(repo, tmp_path)
    by = {r["pr_number"]: r for r in rows2}
    assert len(rows2) == 6 and len(by) == 6
    assert by[fresh["pr_number"]]["title_len"] == 4242
    assert by[legacy["pr_number"]]["title_len"] == 4242
    assert by[dup["pr_number"]]["title_len"] == 4242
    assert by[stale["pr_number"]]["title_len"] != 4242
    assert all(mine_repo.CKPT_STAMP not in r for r in rows2)
    # the shared checkpoint still parses line by line after the torn tail was healed
    assert all(isinstance(x, dict) for x in _lines(ck_path))


def test_changed_flags_remine_everything(repo, tmp_path):
    rows, out = _mine(repo, tmp_path)
    ck = _lines(out + ".partial.jsonl")
    for c in ck:
        c["title_len"] = 4242
    _put(out + ".partial.jsonl", ck)
    rows2, _ = _mine(repo, tmp_path, enable_szz=False)
    assert all(r["title_len"] != 4242 for r in rows2)


def test_resume_with_snapshots_after_a_no_snapshots_run_rebuilds_the_index(repo, tmp_path):
    rows, out = _mine(repo, tmp_path, enable_snapshots=False)
    assert not (tmp_path / "snap" / sc.INDEX_FILE).exists()
    ck = _lines(out + ".partial.jsonl")
    for c in ck:
        c["title_len"] = 4242
    _put(out + ".partial.jsonl", ck)
    rows2, _ = _mine(repo, tmp_path, enable_snapshots=True)
    index = sc.read_jsonl(str(tmp_path / "snap" / sc.INDEX_FILE))
    assert sorted(r["number"] for r in index) == sorted(r["pr_number"] for r in rows2)
    assert len(rows2) == 6
    # no network: every PR counts as outside, so each is in scope and was mined again for its capture
    assert all(r["title_len"] != 4242 for r in rows2)


def test_an_ordinary_resume_remines_nothing(repo, tmp_path, monkeypatch):
    """Same settings, the run 'crashed' with its rows in the checkpoint: every PR comes from the
    checkpoint, none is mined again (no accidental mass re-mine), and the rows are identical."""
    rows, out = _mine(repo, tmp_path)
    assert len(rows) == 6
    mined = []
    real = mine_repo.mine_pr
    monkeypatch.setattr(mine_repo, "mine_pr", lambda *a, **k: (mined.append(a[1].get("number")), real(*a, **k))[1])
    rows2, _ = _mine(repo, tmp_path)
    assert mined == []
    assert sorted(r["pr_number"] for r in rows2) == sorted(r["pr_number"] for r in rows)
    assert [r["title_len"] for r in sorted(rows2, key=lambda r: r["pr_number"])] == \
        [r["title_len"] for r in sorted(rows, key=lambda r: r["pr_number"])]


def test_a_failed_repo_keeps_its_rows_when_another_repo_is_dropped(tmp_path):
    p = tmp_path / "o.partial.jsonl"
    stamp = {"miner": "7.0"}
    _put(p, [{"repo": "a/failed", "pr_number": 1, mine_repo.CKPT_STAMP: stamp},
             {"repo": "b/done", "pr_number": 5, mine_repo.CKPT_STAMP: stamp},
             {"repo": "a/failed", "pr_number": 2, mine_repo.CKPT_STAMP: stamp}])
    mine_repo.drop_repo_from_checkpoint(str(p), "b/done")             # only the finished repo is dropped
    rows, stale, _ = mine_repo.load_checkpoint(str(p), "a/failed", stamp)
    assert sorted(r["pr_number"] for r in rows) == [1, 2] and stale == 0
    assert mine_repo.load_checkpoint(str(p), "b/done", stamp)[0] == []
