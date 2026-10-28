"""The redo report: what a resume repeats because the earlier work came from another miner version. The audit
reads --out only and cannot see it (the checkpoint and the pinned selection are not in --out)."""
from __future__ import annotations

import json
import os
import pathlib
import sqlite3
import sys

import pytest

MINER = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MINER))

import build_dataset as bd  # noqa: E402
import gitio  # noqa: E402
import mine_repo  # noqa: E402

MINE = ".".join(bd.__version__.split(".")[:2])


def _ckpt(out, rows):
    with open(out + ".partial.jsonl", "w", encoding="utf-8") as fh:
        for repo, pr, miner in rows:
            r = {"repo": repo, "pr_number": pr}
            if miner is not None:
                r[bd.CKPT_STAMP] = {"miner": miner, "llm": True}
            fh.write(json.dumps(r) + "\n")


def _selection(cache, stem, major, pad=0):
    os.makedirs(cache, exist_ok=True)
    with open(os.path.join(cache, f"{stem}.selection.json"), "w", encoding="utf-8") as fh:
        json.dump({"params": {"window_from": None, "miner_major": major}, "merged": ["x" * pad]}, fh)


def test_the_checkpoint_key_matches_what_mine_repo_writes():
    assert bd.CKPT_STAMP == mine_repo.CKPT_STAMP


def test_checkpoint_rows_of_another_miner_are_counted_stale_and_unstamped_rows_are_not(tmp_path):
    out = str(tmp_path / "d.parquet")
    _ckpt(out, [("a/b", 1, "6.13"), ("a/b", 2, "6.13"), ("a/b", 3, MINE), ("a/b", 4, None), ("a/b", 1, "6.13")])
    c = bd._checkpoint_by_repo(out)["a/b"]
    assert (c["rows"], c["stale"], c["miners"]) == (4, 2, {"6.13"})       # the last row of PR 1 wins; no double count


def test_a_resume_names_the_repo_the_pr_count_and_the_timeline_cost(tmp_path, capsys):
    out, cache = str(tmp_path / "d.parquet"), str(tmp_path / "ci_cache")
    _ckpt(out, [("facebook/react", i, "6.13") for i in range(4403)])
    _selection(cache, "facebook_react", "6")
    db = sqlite3.connect(os.path.join(cache, "facebook_react.api.sqlite"))
    db.execute("CREATE TABLE kv (k TEXT PRIMARY KEY, t REAL, v BLOB)")
    db.executemany("INSERT INTO kv VALUES (?, 0, x'00')", [("tl:1",), ("tl:2",), ("cc:1",)])
    db.commit(); db.close()
    found = bd._redo_report(out, cache, ["facebook/react", "other/clean"])
    text = capsys.readouterr().out
    assert set(found) == {"facebook/react"}
    assert "4,403 of its 4,403 checkpointed PRs were mined by miner 6.13" in text and "they are kept" in text
    assert "it is kept" in text and "2 timelines are cached" in text
    assert "[version]" in text and "[redo]" not in text and "they are mined again" not in text and "fetched again" not in text
    assert "Re-mine repos" in text and "--remine-repo" in text            # the only way a repo is mined again


def test_a_current_checkpoint_and_selection_say_nothing(tmp_path, capsys):
    out, cache = str(tmp_path / "d.parquet"), str(tmp_path / "ci_cache")
    _ckpt(out, [("a/b", 1, MINE)])
    _selection(cache, "a_b", bd.__version__.split(".")[0])
    assert bd._redo_report(out, cache, ["a/b"]) == {}
    assert capsys.readouterr().out == ""


def test_audit_mode_looks_at_every_checkpoint_and_selection(tmp_path, capsys):
    out, cache = str(tmp_path / "d.parquet"), str(tmp_path / "ci_cache")
    _ckpt(out, [("a/b", 1, "6.13")])
    _selection(cache, "x_y", "6")
    found = bd._redo_report(out, cache)
    assert set(found) == {"a/b", "x_y"}
    assert bd._redo_report(str(tmp_path / "none.parquet"), str(tmp_path / "empty")) == {}
    assert "no checkpoint or pinned selection from another miner version" in capsys.readouterr().out


def test_the_selection_major_is_read_without_loading_the_file(tmp_path):
    cache = str(tmp_path / "ci_cache")
    _selection(cache, "big", "6", pad=50_000_000)               # 50 MB after the params
    assert bd._selection_miner_major(cache, "big") == "6"
    assert bd._selection_miner_major(cache, "missing") is None


def test_the_audit_reads_only_the_columns_it_uses(tmp_path):
    pd = pytest.importorskip("pandas")
    pytest.importorskip("pyarrow")
    out = str(tmp_path / "d.parquet")
    pd.DataFrame({"repo": ["a/b"], "miner_version": ["7.0.0"], "AGE": [1.0], "big_text": ["x" * 100]}).to_parquet(out)
    path, df = bd._audit_frame(out)
    assert list(df.columns) == ["repo", "miner_version", "AGE"]


def test_kill_children_is_a_no_op_without_process_groups(monkeypatch):
    monkeypatch.delattr(mine_repo.os, "getpgrp", raising=False)
    mine_repo._kill_children()                                  # Windows has no os.getpgrp: this used to raise


def test_git_never_starts_an_automatic_repack():
    cmd = gitio._git_cmd("/r", "fetch")
    assert "gc.auto=0" in cmd and "maintenance.auto=false" in cmd


def test_timeline_progress_is_logged(monkeypatch, capsys):
    class Api:
        owner, repo = "o", "r"
        def pr_timeline(self, n):
            return []
    monkeypatch.setattr(mine_repo, "TIMELINE_LOG_EVERY", 2)
    prs = [{"number": i} for i in range(5)]
    st = mine_repo.config.Settings() if hasattr(mine_repo.config, "Settings") else None
    mine_repo._prefetch_timelines(Api(), prs, st)
    err = capsys.readouterr().err
    assert "[timeline] 2/5 read" in err and "[timeline] 4/5 read" in err and "read 5 PR timelines" in err


def _prs(n=3):
    return [{"number": i, "head": {"sha": f"h{i}"}} for i in range(1, n + 1)]


def test_open_snapshots_are_kept_in_the_selection_and_restored(tmp_path, monkeypatch):
    sel = tmp_path / "o_r.selection.json"
    st = mine_repo.config.Settings()
    mine_repo.save_selection(str(sel), st, {"merged": [], "unmerged": []})
    prs = _prs()
    for p in prs[:2]:
        p["_open"] = {"base": "b", "head": f"c{p['number']}", "kind": "exact", "behind": 0}
        p["_pushes"] = [["2026-01-01T00:00:00Z", "x"]]
    prs[2]["_pushes"] = None
    mine_repo._save_open_snapshots(str(sel), st, prs, "main")
    monkeypatch.setattr(mine_repo.gitio, "existing_commits", lambda repo, shas: set(shas))
    fresh = _prs()
    assert mine_repo._restore_open_snapshots("/r", str(sel), st, fresh, "main") is True
    assert fresh[0]["_open"]["head"] == "c1" and fresh[0]["_pushes"][0][1] == "x"
    assert "_open" not in fresh[2] and fresh[2]["_pushes"] is None


def test_open_snapshots_are_not_restored_when_anything_differs(tmp_path, monkeypatch):
    sel = tmp_path / "o_r.selection.json"
    st = mine_repo.config.Settings()
    mine_repo.save_selection(str(sel), st, {"merged": []})
    prs = _prs()
    prs[0]["_open"] = {"base": "b", "head": "c1", "kind": "exact", "behind": 0}
    mine_repo._save_open_snapshots(str(sel), st, prs, "main")
    monkeypatch.setattr(mine_repo.gitio, "existing_commits", lambda repo, shas: set(shas))
    assert mine_repo._restore_open_snapshots("/r", str(sel), st, _prs(), "trunk") is False          # another default branch
    assert mine_repo._restore_open_snapshots("/r", str(sel), st, _prs(4), "main") is False          # a PR it never saw
    monkeypatch.setattr(mine_repo.gitio, "existing_commits", lambda repo, shas: set())
    assert mine_repo._restore_open_snapshots("/r", str(sel), st, _prs(), "main") is False           # re-cloned: heads gone
    st.rework_grace_s += 1
    monkeypatch.setattr(mine_repo.gitio, "existing_commits", lambda repo, shas: set(shas))
    assert mine_repo._restore_open_snapshots("/r", str(sel), st, _prs(), "main") is False           # another grace


def test_unobserved_heads_are_never_kept(tmp_path):
    sel = tmp_path / "o_r.selection.json"
    st = mine_repo.config.Settings()
    mine_repo.save_selection(str(sel), st, {"merged": []})
    prs = _prs()
    prs[1]["_head_unobserved"] = True
    mine_repo._save_open_snapshots(str(sel), st, prs, "main")
    assert "open_snaps" not in json.loads(sel.read_text())
