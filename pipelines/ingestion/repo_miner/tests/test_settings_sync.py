"""A joined PC runs the lead's current shared settings (read with its own token), keeps its machine flags, and
keeps the rows it already mined, reporting which of them used other settings."""
from __future__ import annotations

import os
import sys
import pathlib

import pytest

MINER = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MINER))

import build_dataset as bd  # noqa: E402
import join  # noqa: E402
import sync  # noqa: E402

LEAD = ["--repos-file", "repos.txt", "--max-prs", "20", "--no-llm"]
MINE = ["--repos-file", "repos.txt", "--max-prs", "20", "--llm", "--workers", "3", "--clone-dir", "clones"]


def test_machine_flags_are_not_shared_and_shared_flags_are_not():
    argv = MINE + ["--shard", "2/3"]
    assert "--workers" in join.machine_argv(argv) and "--shard" in join.machine_argv(argv)
    assert "--llm" not in join.machine_argv(argv) and "--repos-file" not in join.machine_argv(argv)


def test_adopted_argv_takes_the_leads_shared_flags_and_keeps_this_pcs_machine_flags():
    new = join.adopted_argv(MINE + ["--shard", "2/3"], LEAD)
    args = bd.build_parser().parse_args(new)
    assert args.llm is False                                     # the lead's --no-llm
    assert args.workers == 3 and args.shard == "2/3" and args.clone_dir == "clones"   # this PC's own


def test_differences_name_each_setting_that_moved():
    diffs = {k for k, _, _ in join.differences(LEAD, MINE)}
    assert "llm" in diffs


def test_a_joined_pc_adopts_the_store_flags_with_its_own_token(monkeypatch, capsys):
    monkeypatch.setenv("RIFFLE_SYNC_URL", "https://store.invalid")
    monkeypatch.setenv("RIFFLE_SYNC_TOKEN", "pc-token")
    monkeypatch.setattr(sync, "fetch_split", lambda url, tok, **kw: {
        "flags": LEAD, "settings_version": "abc123def456"})
    args, version = bd._adopt_lead_settings(MINE + ["--shard", "2/3"], bd.build_parser().parse_args(MINE + ["--shard", "2/3"]))
    assert version == "abc123def456"
    assert args.llm is False and args.workers == 3
    out = capsys.readouterr().out
    assert "[settings] llm:" in out and "using the lead's" in out


def test_not_joined_or_no_answer_keeps_this_pcs_own_flags(monkeypatch, capsys):
    monkeypatch.delenv("RIFFLE_SYNC_URL", raising=False)
    monkeypatch.delenv("RIFFLE_SYNC_TOKEN", raising=False)
    args, version = bd._adopt_lead_settings(MINE, bd.build_parser().parse_args(MINE))
    assert version is None and args.llm is True
    monkeypatch.setenv("RIFFLE_SYNC_URL", "https://store.invalid")
    monkeypatch.setenv("RIFFLE_SYNC_TOKEN", "pc-token")
    monkeypatch.setattr(sync, "fetch_split", lambda *a, **k: None)
    args, version = bd._adopt_lead_settings(MINE, bd.build_parser().parse_args(MINE))
    assert version is None and args.llm is True
    assert "using this PC's own flags" in capsys.readouterr().out


def test_a_test_run_never_adopts(monkeypatch):
    monkeypatch.setenv("RIFFLE_SYNC_URL", "https://store.invalid")
    monkeypatch.setenv("RIFFLE_SYNC_TOKEN", "pc-token")
    monkeypatch.setattr(sync, "fetch_split", lambda *a, **k: pytest.fail("a test run must not read the store"))
    args = bd.build_parser().parse_args(MINE + ["--test"])
    assert bd._adopt_lead_settings(MINE + ["--test"], args) == (args, None)


def test_the_record_keeps_each_repos_version_and_writes_atomically(tmp_path):
    out = str(tmp_path / "d.parquet")
    bd._record_settings(out, "Owner/Name", "v1")
    bd._record_settings(out, "other/repo", "v1")
    bd._record_settings(out, "owner/name", "v2")
    assert bd._load_settings_record(out) == {"owner/name": "v2", "other/repo": "v1"}
    assert not os.path.exists(out + ".settings.json.tmp")


def test_no_version_records_nothing(tmp_path):
    out = str(tmp_path / "d.parquet")
    bd._record_settings(out, "a/b", None)
    assert not os.path.exists(out + ".settings.json")


def test_the_report_names_repos_on_other_and_unknown_settings_and_keeps_them(tmp_path, capsys):
    out = str(tmp_path / "d.parquet")
    bd._record_settings(out, "a/b", "old")
    bd._record_settings(out, "c/d", "new")
    bd._settings_report(out, "new", {"a/b": 5, "c/d": 3, "e/f": 1})
    text = capsys.readouterr().out
    assert "other settings, kept as they are: a/b" in text
    assert "e/f" not in text                                      # no record: the version check decides, not this report
    assert "c/d" not in text                                      # its version matches: nothing to say
    assert bd._load_settings_record(out)["a/b"] == "old"          # the report changes nothing


def test_the_report_is_silent_without_a_version_or_repos(tmp_path, capsys):
    bd._settings_report(str(tmp_path / "d.parquet"), None, {"a/b": 1})
    bd._settings_report(str(tmp_path / "d.parquet"), "new", {})
    assert capsys.readouterr().out == ""


def test_auto_remine_is_a_lead_setting_that_reaches_pcs():
    lead = LEAD + ["--auto-remine"]
    assert "--auto-remine" in join.shared_argv(lead)
    assert "--auto-remine" not in join.machine_argv(lead)
    assert ("auto_remine", True, False) in join.differences(lead, LEAD + ["--no-auto-remine"])
    args = bd.build_parser().parse_args(join.adopted_argv(MINE, lead))
    assert args.auto_remine is True
