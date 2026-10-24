"""Rows mined by a miner older than 7.0.0 are stale: --audit lists them for --auto-remine, and every batch says
which repos in its share are stale before it starts."""
from __future__ import annotations

import json
import os
import pathlib
import sys

import pytest

MINER = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MINER))

import build_dataset as bd  # noqa: E402


def _rows(path, rows):
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def _row(repo, pr, version=None, **extra):
    r = {"repo": repo, "pr_number": pr, "AGE": 1.0, "NUC": 1, "file_fix_density": 0.0,
         "max_file_top_author_share": 0.5}                 # blame data present, so only the version decides
    if version is not None:
        r["miner_version"] = version
    r.update(extra)
    return r


def test_old_and_unversioned_rows_are_stale_current_rows_are_not(tmp_path):
    pytest.importorskip("pandas")
    out = str(tmp_path / "d.jsonl")
    _rows(out, [_row("a/old", 1, "6.13.3"), _row("a/old", 2, "6.13.3"), _row("b/none", 1),
                _row("c/new", 1, "7.0.0"), _row("c/new", 2, "7.0.1")])
    stale = bd._stale_by_version(out)
    assert set(stale) == {"a/old", "b/none"}
    assert "6.13.3" in stale["a/old"] and "no miner version" in stale["b/none"]


def test_a_repo_with_one_old_row_is_stale(tmp_path):
    pytest.importorskip("pandas")
    out = str(tmp_path / "d.jsonl")
    _rows(out, [_row("a/mixed", 1, "7.0.0"), _row("a/mixed", 2, "6.12.0")])
    assert set(bd._stale_by_version(out)) == {"a/mixed"}


def test_no_output_means_nothing_is_stale(tmp_path):
    assert bd._stale_by_version(str(tmp_path / "missing.jsonl")) == {}


def test_audit_puts_version_stale_repos_on_the_remine_list(tmp_path, capsys):
    pytest.importorskip("pandas")
    out = str(tmp_path / "d.jsonl")
    _rows(out, [_row("a/old", i, "6.13.3") for i in range(12)] + [_row("c/new", i, "7.0.0") for i in range(12)])
    bad = bd._audit(out)
    text = capsys.readouterr().out
    assert "a/old" in bad and "c/new" not in bad
    assert "these repos were mined by an older miner than 7.0.0" in text
    assert "a/old: mined by 6.13.3" in text


def test_audit_says_so_when_every_row_is_current(tmp_path, capsys):
    pytest.importorskip("pandas")
    out = str(tmp_path / "d.jsonl")
    _rows(out, [_row("c/new", i, "7.0.0") for i in range(12)])
    bd._audit(out)
    assert "every row was mined by 7.0.0 or later" in capsys.readouterr().out


def test_version_parsing_compares_minor_not_text():
    assert bd._major_minor("6.13.3") < bd.COMPAT_MINOR
    assert bd._major_minor("7.0.0") >= bd.COMPAT_MINOR
    assert bd._major_minor("10.1.0") >= bd.COMPAT_MINOR
    assert bd._major_minor("junk") is None


def test_auto_remine_is_on_by_default_and_can_be_turned_off():
    assert bd.build_parser().parse_args([]).auto_remine is True
    assert bd.build_parser().parse_args(["--no-auto-remine"]).auto_remine is False
    assert bd.build_parser().parse_args(["--auto-remine"]).auto_remine is True


def test_an_automatic_remine_is_recorded_per_repo_and_version(tmp_path, monkeypatch):
    out = str(tmp_path / "d.parquet")
    assert bd._load_auto_remined(out) == {}
    bd._mark_auto_remined(out, ["A/Old", "b/x"])
    assert bd._load_auto_remined(out) == {"a/old": bd.__version__, "b/x": bd.__version__}
    assert not os.path.exists(bd._auto_remined_path(out) + ".tmp")


def test_a_record_from_another_version_does_not_block_a_remine(tmp_path, monkeypatch):
    out = str(tmp_path / "d.parquet")
    with open(bd._auto_remined_path(out), "w", encoding="utf-8") as fh:
        json.dump({"a/old": "6.13.3"}, fh)
    rec = bd._load_auto_remined(out)
    assert rec.get("a/old") != bd.__version__            # so it may be re-mined once under this version


def test_rows_from_a_newer_miner_are_not_auto_remined(tmp_path):
    pytest.importorskip("pandas")
    out = str(tmp_path / "d.jsonl")
    _rows(out, [_row("a/newer", 1, "7.0.1"), _row("b/same", 1, "7.0.0"), _row("c/older", 1, "6.13.3")])
    newer = bd._mined_by_newer(out)
    assert newer == {"a/newer": "7.0.1"}


def test_version_keys_compare_patch_levels():
    assert bd._version_key("7.0.1") > bd._version_key("7.0.0")
    assert bd._version_key("7.1.0") > bd._version_key("7.0.9")
    assert bd._version_key("junk") is None
