"""build_dataset's repo-list parsing and option checks: what a repos file line may name, how tags
read, duplicates, and the combinations of flags refused before anything is cloned."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import build_dataset as bd  # noqa: E402
import join  # noqa: E402


def _run(*argv, repos=None, tmp=None):
    env = dict(os.environ, RIFFLE_TRACE=str(tmp / "t.jsonl"))
    return subprocess.run([sys.executable, bd.__file__, "--no-llm", "--no-sync", *argv],
                          capture_output=True, text=True, timeout=60, cwd=str(tmp), env=env)


# ---- _parse_url -------------------------------------------------------------------

@pytest.mark.parametrize("line,want", [
    ("https://github.com/pallets/flask", ("pallets", "flask")),
    ("https://github.com/pallets/flask.git", ("pallets", "flask")),
    ("https://github.com/pallets/flask/  # Train [~5 commits]", ("pallets", "flask")),
    ("git@github.com:pallets/flask.git", ("pallets", "flask")),
    ("https://github.com/pallets/flask/tree/main", ("pallets", "flask")),       # a pasted browser URL names its repo
    ("https://github.com/pallets/flask?tab=readme-ov-file", ("pallets", "flask")),
    ("https://github.com/o/my.repo_x-1", ("o", "my.repo_x-1")),
])
def test_parse_url_reads_owner_and_name(line, want):
    got = bd._parse_url(line)
    assert got is not None and got[:2] == want


@pytest.mark.parametrize("line", [
    "", "   ", "# a comment", "not a url", "https://github.com/onlyowner",
    "https://github.com/o/..", "https://github.com/o/.", "https://github.com/../r",
    "https://github.com/o/na me", "https://github.com/o/na$me", "https://github.com/o/na;rm -rf",
    "https://github.com/o/--upload-pack=x",
    "https://github.com/o/-rf", "https://github.com/-o/r",
])
def test_parse_url_rejects_what_cannot_be_a_repo(line):
    assert bd._parse_url(line) is None


def test_the_clone_url_is_rebuilt_from_the_validated_names():
    assert bd._canonical_url("o", "r") == "https://github.com/o/r"


def test_a_path_that_leaves_the_clone_dir_is_refused(tmp_path):
    base = tmp_path / "clones"
    base.mkdir()
    assert bd._safe_path(str(base), "o_r") == str(base.resolve() / "o_r")
    assert bd._safe_path(str(base), "..") is None
    assert bd._safe_path(str(base), "../x") is None
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (base / "o_r").symlink_to(outside)
    assert bd._safe_path(str(base), "o_r") is None            # a link planted in the clone dir


def test_drop_repo_refuses_a_name_that_is_not_a_repo(tmp_path):
    with pytest.raises(ValueError):
        bd._drop_repo(str(tmp_path / "d.jsonl"), "o/..", cache_dir=str(tmp_path))


# ---- _parse_domain ----------------------------------------------------------------

def test_domain_tag_is_case_insensitive_and_lowercased():
    assert bd._parse_domain("https://github.com/o/r  # [domain: web-ts]") == "web-ts"
    assert bd._parse_domain("https://github.com/o/r  # [Domain: Web-TS]") == "web-ts"
    assert bd._parse_domain("https://github.com/o/r  # [ DOMAIN :web-ts ]") == "web-ts"
    assert bd._parse_domain("https://github.com/o/r") is None


def test_a_malformed_domain_tag_is_warned_about_and_read_as_untagged(capsys):
    assert bd._parse_domain("https://github.com/o/r  # [domain: web ts!]") is None
    assert "malformed domain tag" in capsys.readouterr().out
    assert bd._parse_domain("https://github.com/o/r  # [domain: ]") is None


# ---- duplicates -------------------------------------------------------------------

def test_a_repo_listed_twice_is_kept_once_with_a_warning(capsys):
    bd._WARNED.clear()
    lines = ["https://github.com/Pallets/Flask # first\n", "https://github.com/o/r\n",
             "https://github.com/pallets/flask.git\n"]
    assert bd._dedupe_lines(lines) == lines[:2]
    assert "listed twice" in capsys.readouterr().out


# ---- the command line -------------------------------------------------------------

def test_only_domain_with_no_match_says_so_and_differs_from_no_urls(tmp_path):
    repos = tmp_path / "repos.txt"
    repos.write_text("https://github.com/a/one  # [domain: web-ts] [~10 commits]\n")
    r = _run("--repos-file", str(repos), "--only-domain", "ml-py", tmp=tmp_path)
    assert r.returncode != 0 and "0 repos tagged" in r.stderr and "no valid GitHub URLs" not in r.stderr
    empty = tmp_path / "empty.txt"
    empty.write_text("# nothing\n")
    r = _run("--repos-file", str(empty), "--only-domain", "ml-py", tmp=tmp_path)
    assert "no valid GitHub URLs" in r.stderr


def test_only_domain_is_case_insensitive(tmp_path):
    repos = tmp_path / "repos.txt"
    repos.write_text("https://github.com/a/one  # [Domain: Web-TS] [~10 commits]\n")
    r = _run("--repos-file", str(repos), "--only-domain", "WEB-ts", "--shard-plan", "1", tmp=tmp_path)
    assert r.returncode == 0 and "a/one" in r.stdout


@pytest.mark.parametrize("n", ["0", "-2"])
def test_shard_plan_below_one_exits_cleanly(tmp_path, n):
    r = _run("--shard-plan", n, tmp=tmp_path)
    assert r.returncode != 0 and "at least 1 PC" in r.stderr and "Traceback" not in r.stderr


def _parse(*argv):
    return bd.build_parser().parse_args(list(argv))


@pytest.mark.parametrize("argv,needle", [
    (["--full-repo", "--lead-in-days", "30"], "--full-repo"),
    (["--full-repo", "--window-from", "2024-01-01", "--window-to", "2024-02-01"], "--full-repo"),
    (["--full-repo", "--max-prs", "5"], "--full-repo"),
    (["--window-from", "2024-01-01"], "go together"),
    (["--window-to", "2024-01-01"], "go together"),
    (["--window-from", "2024-13-01", "--window-to", "2024-12-01"], "YYYY-MM-DD"),
    (["--window-from", "2024-06-01", "--window-to", "2024-01-01"], "is after"),
    (["--min-age-days", "-1"], "--min-age-days"),
    (["--llm-sample-pct", "101"], "0 to 100"),
    (["--llm-sample-pct", "-1"], "0 to 100"),
    (["--max-prs", "-3"], "--max-prs"),
    (["--shard", "3/2"], "K/N"),
    (["--shard", "x"], "K/N"),
    (["--remine-repo", "o/.."], "OWNER/NAME"),
    (["--max-files-deep", "-1"], "--max-files-deep"),
])
def test_bad_option_combinations_are_found_before_anything_runs(argv, needle):
    problems = bd.validate_options(_parse(*argv))
    assert problems and any(needle in p for p in problems), problems


def test_good_option_combinations_pass():
    assert bd.validate_options(_parse()) == []
    assert bd.validate_options(_parse("--full-repo")) == []
    assert bd.validate_options(_parse("--window-from", "2024-01-01", "--window-to", "2024-06-30", "--max-prs", "50")) == []
    assert bd.validate_options(_parse("--min-age-days", "0", "--llm-sample-pct", "0", "--shard", "2/3")) == []


def test_max_prs_zero_means_no_cap_on_both_sides():
    args = _parse("--max-prs", "0", "--full-repo")             # the miner's truthiness rule: 0 is no cap, so no clash
    assert bd.validate_options(args) == [] and args.max_prs is None


def test_a_bad_combination_exits_before_cloning(tmp_path):
    r = _run("--full-repo", "--lead-in-days", "30", tmp=tmp_path)
    assert r.returncode != 0 and "Nothing was cloned" in r.stderr
    assert not (tmp_path / "cloned_repos").exists()


# ---- the lead's repo list (join.apply_split) --------------------------------------

def test_the_leads_repo_list_is_checked_line_by_line():
    text = ("# list\nhttps://github.com/a/one  # [~10 commits]\n\nhttps://github.com/b/..\n"
            "https://evil.example/x/y\nhttps://github.com/c/two\x07\n")
    cleaned, rejected = join.clean_repos_text(text)
    assert "a/one" in cleaned and "# list" in cleaned
    assert len(rejected) == 3 and "b/.." in rejected[0]
    assert "evil" not in cleaned


def test_a_split_whose_list_has_no_valid_repo_is_not_applied(tmp_path):
    doc = {"s": "split1", "k": 1, "n": 2, "a": ["--repos-file", "x"]}
    with pytest.raises(ValueError):
        join.apply_split(doc, {"repos_name": "r.txt", "repos_text": "https://github.com/../..\n"}, str(tmp_path))


def test_join_shaping_carries_the_llm_and_depth_settings():
    for k in ("llm_max_diff_chars", "llm_diff_context", "llm_backend", "max_files_deep"):
        assert k in join.SHAPING
    s = join.shaping(["--llm-diff-context", "3", "--llm-backend", "api", "--max-files-deep", "50"])
    assert (s["llm_diff_context"], s["llm_backend"], s["max_files_deep"]) == (3, "api", 50)
