"""The shard plan shows PR counts from the repos file's [N PRs] tags."""
from __future__ import annotations

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import build_dataset  # noqa: E402


def test_parse_prs():
    assert build_dataset._parse_prs("https://github.com/o/r  # [~1,200 commits] [8,712 PRs]") == 8712
    assert build_dataset._parse_prs("https://github.com/o/r  # [~1,200 commits]") is None


def test_plan_lists_prs_per_repo_and_per_pc(tmp_path):
    repos = tmp_path / "repos.txt"
    repos.write_text(
        "https://github.com/a/one  # [~2,000 commits] [500 PRs]\n"
        "https://github.com/b/two  # [~1,000 commits] [250 PRs]\n"
        "https://github.com/c/three  # [~900 commits]\n")
    out = subprocess.run([sys.executable, build_dataset.__file__, "--repos-file", str(repos),
                          "--shard-plan", "2"], capture_output=True, text=True, timeout=60).stdout
    assert "a/one  (~2,000 commits, 500 PRs)" in out
    assert "c/three  (~900 commits)" in out                 # untagged PRs: no PR note
    assert "PC 1/2: 2 repos, ~2,900 commits, 500 PRs" in out   # equal repo counts: one + three


def test_plan_needs_no_llm_backend(tmp_path):
    repos = tmp_path / "repos.txt"
    repos.write_text("https://github.com/a/one  # [~2,000 commits] [500 PRs]\n")
    env = {**os.environ, "LLM_BACKEND": "api", "LLM_API_KEY": ""}
    p = subprocess.run([sys.executable, build_dataset.__file__, "--repos-file", str(repos),
                        "--shard-plan", "1"], capture_output=True, text=True, timeout=60, env=env)
    assert p.returncode == 0 and "a/one" in p.stdout


def _repos(tmp_path):
    f = tmp_path / "repos.txt"
    # commits and PRs disagree: "big_commits" is heavy in commits, "big_prs" is heavy in PRs
    f.write_text("".join(f"https://github.com/o/{n}  # [~{c:,} commits] [{p:,} PRs]\n" for n, c, p in (
        ("big_commits", 9000, 100), ("big_prs", 1000, 9000), ("mid_a", 3000, 3000), ("mid_b", 3000, 3000),
        ("small_a", 500, 400), ("small_b", 500, 400))))
    return str(f)


def test_the_split_can_balance_by_commits_or_by_prs(tmp_path):
    repos = _repos(tmp_path)
    by_c = build_dataset.plan_shares(repos, None, 2, "commits")
    by_p = build_dataset.plan_shares(repos, None, 2, "prs")
    pr = {"big_commits": 100, "big_prs": 9000, "mid_a": 3000, "mid_b": 3000, "small_a": 400, "small_b": 400}
    spread = lambda shares: abs(sum(pr[n.split("/")[1]] for n, _w in shares[0]) - sum(pr[n.split("/")[1]] for n, _w in shares[1]))  # noqa: E731
    assert spread(by_p) < spread(by_c), "balancing by PRs must even out the PRs"
    for shares in (by_c, by_p):                                          # either way: disjoint and complete
        names = [n for sh in shares for n, _w in sh]
        assert sorted(names) == sorted(set(names)) and len(names) == 6


def test_split_id_depends_on_what_the_split_balances_by(tmp_path):
    entries = [("o", f"r{i}", "u") for i in range(4)]
    w = [10, 20, 30, 40]
    assert build_dataset.split_id(entries, w, 2) == build_dataset.split_id(entries, w, 2, "commits")      # old IDs are unchanged
    assert build_dataset.split_id(entries, w, 2, "prs") != build_dataset.split_id(entries, w, 2, "commits")


def test_the_plan_names_what_it_balanced_by(tmp_path):
    repos = _repos(tmp_path)
    run = lambda by: subprocess.run([sys.executable, build_dataset.__file__, "--repos-file", repos, "--shard-plan", "2", "--split-by", by],  # noqa: E731
                                    capture_output=True, text=True, timeout=60).stdout
    prs, commits = run("prs"), run("commits")
    assert "balanced by prs" in prs and "balanced by commits" in commits
    sid = lambda out: out.split("split ID ")[1].split(":")[0]                                           # noqa: E731
    assert sid(prs) != sid(commits) and sid(prs) == sid(run("prs")), "deterministic, and different for the two ways"
