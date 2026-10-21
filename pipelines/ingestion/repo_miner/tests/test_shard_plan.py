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
