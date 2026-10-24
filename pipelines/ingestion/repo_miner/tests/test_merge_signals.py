"""Merge-model signals: body/agent parsing and the prior-only repo columns."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from features import merge_signals as ms  # noqa: E402

ST = config.Settings()
ST.merge_signal_min_prs = 2


def test_body_features():
    body = ("## Summary\nFixes #12\n\n## Test plan\n- [x] unit tests\n- [ ] docs\n\n"
            "🤖 Generated with [Claude Code](https://claude.com/claude-code)")
    f = ms.body_features(body)
    assert f == {"has_linked_issue": True, "body_headings": 2, "body_checklist_items": 2,
                 "body_has_test_plan": True, "agent_boilerplate": True}
    assert ms.body_features(None)["has_linked_issue"] is False
    assert ms.body_features("see https://github.com/o/r/issues/5")["has_linked_issue"] is True
    assert ms.body_features("prefix #12 suffix")["has_linked_issue"] is False


def test_agent_name():
    assert ms.agent_name("alice", "codex/fix-thing", None, "") == "codex"
    assert ms.agent_name("Copilot", "copilot/x", None, "") == "copilot"
    assert ms.agent_name("bob", "main", None, "Generated with Claude Code") == "claude_code"
    assert ms.agent_name("devin", "feature/x", "devin@example.com", "") is None


def test_agent_names_need_the_whole_login_and_a_slash_branch_prefix():
    # people whose names start like an agent's are not agents
    assert ms.agent_name("julesb", "fix-typo", None, "") is None
    assert ms.agent_name("jules-verne", "main", None, "") is None
    assert ms.agent_name("claude-docs", "claude-docs", None, "") is None
    assert ms.agent_name("cursor-fix", "cursor-fix", None, "") is None
    assert ms.agent_name("alice", "jules-fix", None, "") is None
    assert not ms._BRANCH_PREFIX_RE.match("claude-docs") and not ms._BRANCH_PREFIX_RE.match("cursor-fix")
    # the real agents
    assert ms.agent_name("jules[bot]", "main", None, "") == "jules"
    assert ms.agent_name("google-labs-jules[bot]", "main", None, "") == "jules"
    assert ms.agent_name("alice", "jules/fix", None, "") == "jules"
    assert ms.agent_name("claude[bot]", "main", None, "") == "claude_code"
    assert ms.agent_name("alice", "claude/fix", None, "") == "claude_code"
    assert ms.agent_name("cursor[bot]", "main", None, "") == "cursor"
    assert ms.agent_name("devin-ai-integration[bot]", "main", None, "") == "devin"
    assert ms.agent_name("alice", "main", "1+devin-ai-integration[bot]@users.noreply.github.com", "") == "devin"
    assert ms.agent_name("Copilot", "main", None, "") == "copilot"
    assert ms.agent_name("copilot-swe-agent[bot]", "main", None, "") == "copilot"
    assert ms.agent_name("copilotfan", "main", None, "") is None


def _row(n, opened, closed, merged, outsider=True, agent=None, first=None, toks=()):
    return {"pr_number": n, ms.SIDECAR: {"opened": opened, "closed": closed, "merged": merged,
                                        "outsider": outsider, "agent": agent,
                                        "first_response": first, "tokens": list(toks)}}


def test_repo_columns_are_prior_only():
    rows = [
        _row(1, "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z", True, agent="codex",
             first="2026-01-01T02:00:00Z", toks=("cache", "redis")),
        _row(2, "2026-01-01T00:00:00Z", "2026-01-03T00:00:00Z", False, agent="codex",
             first="2026-01-01T04:00:00Z"),
        # closes AFTER #4 opens: must not count for #4
        _row(3, "2026-01-01T00:00:00Z", "2026-01-20T00:00:00Z", False, toks=("cache", "redis", "ttl")),
        _row(4, "2026-01-10T00:00:00Z", "2026-01-11T00:00:00Z", True, agent="codex",
             toks=("cache", "redis")),
    ]
    ms.compute_repo(rows, ST, with_unmerged=True)
    r4 = rows[3]
    assert r4["repo_outside_closed_count"] == 2 and r4["repo_outside_merge_rate"] == 0.5
    assert r4["agent_recent_closed_count"] == 2 and r4["agent_recent_merge_rate"] == 0.5
    assert r4["repo_median_first_response_hours"] == 3.0
    assert r4["repo_agent_pr_load_30d"] == 2
    assert r4["max_sim_open_prs"] == round(2 / 3, 4)          # #3 still open at #4's open
    assert r4["max_sim_recent_merged"] == 1.0                 # #1 merged on Jan 2
    assert rows[0]["repo_outside_merge_rate"] is None         # no prior PRs


def test_merge_rates_null_without_unmerged_rows():
    rows = [_row(i, f"2026-01-0{i}T00:00:00Z", f"2026-01-0{i}T01:00:00Z", True) for i in range(1, 6)]
    ms.compute_repo(rows, ST, with_unmerged=False)
    assert rows[-1]["repo_outside_merge_rate"] is None
    assert rows[-1]["repo_outside_closed_count"] == 4


def test_repo_file_patterns():
    for name in ("CONTRIBUTING.md", "docs/contributing.md", ".github/CONTRIBUTING.rst", "contributing"):
        assert ms._CONTRIB_RE.match(name), name
    assert not ms._CONTRIB_RE.match("src/contributing.py")
    for name in ("AGENTS.md", "claude.md", ".cursorrules", ".github/copilot-instructions.md"):
        assert ms._AGENTS_RE.match(name), name
