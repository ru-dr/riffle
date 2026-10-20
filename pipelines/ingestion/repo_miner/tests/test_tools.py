"""scout.py, llm_compare.py and audit_export.py."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import audit_export  # noqa: E402
import config  # noqa: E402
import llm_compare  # noqa: E402
import scout  # noqa: E402
from features import llm_flags  # noqa: E402


def _pr(i, assoc="CONTRIBUTOR", merged=True, bot=False, branch="fix/x"):
    login = "dependabot[bot]" if bot else f"u{i}"
    return {"number": i, "user": {"login": login, "type": "Bot" if bot else "User"},
            "author_association": assoc, "merged_at": "2025-01-02T00:00:00Z" if merged else None,
            "head": {"ref": branch}, "body": ""}


def test_scout_stats_and_flags():
    prs = ([_pr(i, merged=i % 2 == 0) for i in range(40)] + [_pr(100 + i, bot=True) for i in range(10)]
           + [_pr(200, branch="codex/thing", merged=False)])
    s = scout.stats(prs)
    assert s["sample"] == 51 and s["bot_share"] == round(10 / 51, 3)
    assert s["agent_share"] == round(1 / 51, 3) and s["flags"] == []
    assert 0 < s["score"] <= 1
    bots = scout.stats([_pr(i, bot=True) for i in range(9)] + [_pr(9, merged=True)])
    assert "mostly bot PRs" in bots["flags"] and "almost no unmerged outside PRs" in bots["flags"]


def test_scout_ranks_repos(monkeypatch):
    lists = {"o/good": [_pr(i, merged=i % 2 == 0) for i in range(50)],
             "o/bots": [_pr(i, bot=True) for i in range(50)]}

    def fake_get(self, path, params=None, body=None):
        return 200, lists[f"{self.owner}/{self.repo}"], {}

    monkeypatch.setattr(scout.GitHubAPI, "_get", fake_get)
    res = scout.scout([("o", "bots", "web-ts"), ("o", "good", "web-ts")], config.Settings())
    assert [r["repo"] for r in res] == ["o/good", "o/bots"]


def test_compare_agreement():
    base = {k: None for k in llm_flags.FLAG_SCHEMA}
    a = {"r#1": dict(base, exception_swallowed=True, change_intent="fix"),
         "r#2": dict(base, exception_swallowed=False, change_intent="feat")}
    b = {"r#1": dict(base, exception_swallowed=True, change_intent="refactor"),
         "r#2": dict(base, exception_swallowed=False, change_intent="feat")}
    rep = llm_compare.agreement({"sonnet": a, "haiku": b}, "sonnet", "haiku")
    assert rep["pairs"] == 2 and rep["exception_swallowed"] == 1.0 and rep["change_intent"] == 0.5
    assert rep["overall"] == 0.75 and rep["semantic_risk_1to5"] is None


def test_compare_sample_needs_scope_and_a_clone(tmp_path):
    (tmp_path / "o_r").mkdir()
    rows = [{"repo": "o/r", "pr_number": i, "author_association": a, "author_is_bot": False,
             "base_sha": "b", "head_sha": "h", "docs_only": d}
            for i, (a, d) in enumerate([("CONTRIBUTOR", False), ("MEMBER", False),
                                        ("NONE", True), ("FIRST_TIMER", False)])]
    rows.append({"repo": "x/notcloned", "pr_number": 9, "author_association": "CONTRIBUTOR",
                 "base_sha": "b", "head_sha": "h"})
    got = llm_compare.sample(rows, str(tmp_path), 10, seed=1)
    assert sorted(r["pr_number"] for r in got) == [0, 3]


def test_audit_draw_is_stratified_and_weights_agents():
    rows = []
    for i in range(700):
        merged = i % 3 != 0
        rows.append({"repo": "o/r", "pr_number": i, "label_merged": merged,
                     "label_rework": merged and i % 2 == 0,
                     "close_reason": None if merged else audit_export.CLASSES[2 + i % 5],
                     "ai_agent_authored": i % 7 == 0})
    picked = audit_export.draw(rows, 70, seed=1)
    counts = {c: sum(1 for r in picked if r["stratum"] == c) for c in audit_export.CLASSES}
    assert all(v == 10 for v in counts.values()), counts
    agents = sum(1 for r in picked if r["ai_agent_authored"])
    assert agents >= 30                     # up to half of each class, given enough agent PRs
    assert len({(r["repo"], r["pr_number"]) for r in picked}) == len(picked)
