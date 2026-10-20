"""Revert detection on squash-merge style subjects (vite-like)."""
from __future__ import annotations

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import labels  # noqa: E402


def test_is_revert_text_prefixed_subjects():
    for s in ('revert: use chokidar v3 (#18659)', 'Revert "x"', 'revert(server): drain stdin (#20885)',
              'fix: revert "perf(deps): replace debug with obug (#21107)"',
              'fix(deps): revert es-module-lexer to 2.1.0 (#22827)',
              'chore: revert "chore: only allow pnpm (#20201)" (#21178)',
              "fix: x\n\nThis reverts commit abc"):
        assert config.is_revert_text(s), s
    for s in ("fix: reverted nothing", "feat: reverts the flag", "fix: prevent revert loop in x",
              "chore: irreverent", ""):
        assert not config.is_revert_text(s), s


def _git(repo, *args, when):
    env = {**os.environ, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when,
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "-C", repo, *args], check=True, env=env, capture_output=True)


def test_revert_delay_matches_pr_number_not_other_numbers(tmp_path):
    repo = str(tmp_path)
    _git(repo, "init", "-q", when="2025-01-01T00:00:00Z")
    for msg, when in (('fix: revert "perf: replace debug (#21107)"', "2025-01-11T00:00:00Z"),
                      ("revert: unrelated change (#21999)", "2025-01-05T00:00:00Z")):
        _git(repo, "commit", "-q", "--allow-empty", "-m", msg, when=when)
    st = config.Settings()
    pr = {"number": 21107, "merge_commit_sha": "f" * 40, "merged_at": "2025-01-01T00:00:00Z",
          "title": "perf: replace debug"}
    assert labels.revert_delay(repo, pr, st) == 10.0
    assert labels.revert_delay(repo, {**pr, "number": 2110}, st) is None   # #2110 is not #21107
    assert labels.revert_delay(repo, {**pr, "number": 555, "title": "zzz"}, st) is None


def test_is_hotfix_text_urgent_phrasings_but_not_ordinary_fixes():
    for t in ("hotfix: crash on start", "fix: hot-fix for the build", "chore: Hot fix", "rollback to vite 4.1",
              "revert: roll back the loader change", "fix: rolled back the cache", "fix: quickfix for ssr",
              "fix: quick fix for css", "fix: urgent fix for HMR", "fix: emergency patch", "critical security fix"):
        assert config.is_hotfix_text(t), t
    for t in ("fix: resolve relative paths", "feat: add urgent flag to the CLI", "fix: hotfixture", "chore: backport",
              "docs: rollbackable steps", "fix: cherry-pick of urgent", "refactor: critical path", ""):
        assert not config.is_hotfix_text(t), t
