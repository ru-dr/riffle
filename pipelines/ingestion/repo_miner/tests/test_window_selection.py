"""Date-window PR selection (proposal 1.2), the history lead-in (1.4), the
bot / agent split (1.3) and the provenance columns (1.7, 1.8)."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import mine_repo  # noqa: E402
from features import llm_flags  # noqa: E402
from github_api import GitHubAPI  # noqa: E402

UTC = timezone.utc


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeAPI(GitHubAPI):
    """GitHubAPI with the closed-PR list served from memory, newest created first."""

    def __init__(self, prs, st):
        super().__init__("o", "r", st)
        self._prs = sorted(prs, key=lambda p: p["created_at"], reverse=True)

    def _get(self, path, params=None, body=None):
        page = (params or {}).get("page", 1)
        return 200, self._prs[(page - 1) * 100: page * 100], {}


def _prs():
    """One closed PR a day through 2025: every 3rd unmerged, every 10th a draft."""
    out = []
    t0 = datetime(2025, 1, 1, tzinfo=UTC)
    for i in range(365):
        created = t0 + timedelta(days=i)
        closed = created + timedelta(days=2)
        out.append({"number": i + 1, "created_at": iso(created), "closed_at": iso(closed),
                    "merged_at": None if i % 3 == 0 else iso(closed), "draft": i % 10 == 9,
                    "user": {"login": f"u{i}", "type": "User"}, "author_association": "CONTRIBUTOR"})
    return out


def _st(**kw):
    st = config.Settings()
    st.min_age_days = 0
    for k, v in kw.items():
        setattr(st, k, v)
    return st


def test_window_takes_every_closed_pr_created_inside_it():
    st = _st(window_from="2025-03-01", window_to="2025-05-31", lead_in_days=30)
    api = FakeAPI(_prs(), st)
    merged = api.window_prs()
    kept = merged + api.unmerged
    created = sorted(p["created_at"] for p in kept)
    assert created[0] >= "2025-03-01" and created[-1] <= "2025-05-31T23:59:59Z"
    assert not any(p["draft"] for p in kept)
    in_window = [p for p in _prs() if "2025-03-01" <= p["created_at"] <= "2025-05-31T23:59:59Z"
                 and not p["draft"]]
    assert len(kept) == len(in_window)
    assert {p["number"] for p in api.unmerged} == {p["number"] for p in in_window if not p["merged_at"]}
    assert all(p.get("_unmerged") for p in api.unmerged)
    # lead-in: the 30 days before the window, metadata only
    lead = sorted(p["created_at"] for p in api.lead_in)
    assert lead[0] >= "2025-01-30" and lead[-1] < "2025-03-01"


def test_cap_keeps_the_newest_contiguous_block():
    st = _st(window_from="2025-01-01", window_to="2025-12-31", max_prs=50, lead_in_days=10)
    api = FakeAPI(_prs(), st)
    kept = api.window_prs() + api.unmerged
    assert len(kept) == 50
    newest = sorted((p for p in _prs() if not p["draft"]), key=lambda p: p["created_at"])[-50:]
    assert {p["number"] for p in kept} == {p["number"] for p in newest}
    assert api.block_start == min(p["created_at"] for p in kept)
    # merged and unmerged come from the same block: the repo's real mix
    assert 0 < len(api.unmerged) < 50
    # the lead-in is the 10 days before the block, not before the window
    block = datetime.fromisoformat(api.block_start.replace("Z", "+00:00"))
    assert all(block - timedelta(days=10) <= datetime.fromisoformat(p["created_at"].replace("Z", "+00:00")) < block
               for p in api.lead_in) and api.lead_in


def test_outcomes_too_recent_are_left_out():
    prs = _prs()
    now = datetime.now(UTC)
    for p in prs[-5:]:
        p["created_at"] = iso(now - timedelta(days=3))
        p["closed_at"] = iso(now - timedelta(days=1))
        p["merged_at"] = p["closed_at"]
    st = _st(window_from="2025-01-01", window_to=now.strftime("%Y-%m-%d"), min_age_days=30, lead_in_days=0)
    kept = FakeAPI(prs, st).window_prs()
    assert not any(p["number"] in {q["number"] for q in prs[-5:]} for p in kept)


# ---- bots and agents (1.3) ----------------------------------------------------------

def test_dependency_bots_are_bots_not_agents():
    assert not mine_repo._is_agent_identity("49699333+dependabot[bot]@users.noreply.github.com",
                                            "dependabot[bot]")
    assert mine_repo._is_bot_identity("49699333+dependabot[bot]@users.noreply.github.com",
                                      "dependabot[bot]")
    assert mine_repo._is_agent_identity("198982749+Copilot@users.noreply.github.com"
                                        .lower().replace("copilot", "copilot"), "Copilot")
    assert mine_repo._is_agent_identity("noreply@anthropic.com", "Claude")


def test_agent_prs_stay_in_llm_scope():
    st = _st(llm_sample_pct=0)
    pr = {"number": 1, "author_association": "NONE", "user": {"login": "Copilot", "type": "Bot"}}
    assert mine_repo._llm_in_scope(pr, {"repo": "o/r", "author_is_bot": False, "ai_agent_authored": True}, st)
    assert not mine_repo._llm_in_scope(pr, {"repo": "o/r", "author_is_bot": True, "ai_agent_authored": False}, st)


# ---- provenance (1.7, 1.8) -----------------------------------------------------------

def test_provenance_columns():
    st = _st(domain="web-ts")
    p = mine_repo._provenance(st)
    assert set(p) == set(mine_repo.PROVENANCE_COLUMNS)
    assert p["miner_version"] and p["miner_commit"] and p["domain"] == "web-ts"
    assert p["llm_model"] == st.llm_model and p["llm_prompt_version"] == llm_flags.prompt_version()
    st.enable_llm = False
    assert mine_repo._provenance(st)["llm_model"] is None
    assert len(llm_flags.prompt_version()) == 12


# ---- health check (Part 3) ---------------------------------------------------------

def test_health_flags():
    import health
    rows = [{"label_merged": True, "author_association": "CONTRIBUTOR", "author_is_bot": False,
             "label_bad": False, "label_ci_fail": None} for _ in range(10)]
    h = health.check(rows)
    assert any("only 10 rows" in f for f in h["flags"])
    assert any("almost no unmerged outside PRs" in f for f in h["flags"])
    assert any("CI unobserved" in f for f in h["flags"])
    rows += [{"label_merged": False, "author_association": "NONE", "author_is_bot": True,
              "close_reason": "other"} for _ in range(30)]
    assert any("bot PRs are" in f for f in health.check(rows)["flags"])


# ---- pinned selection (resume) ---------------------------------------------------

def test_selection_round_trip_and_settings_change(tmp_path):
    st = _st(window_from="2025-01-01", window_to="2025-06-30", max_prs=100)
    path = str(tmp_path / "o_r.selection.json")
    mine_repo.save_selection(path, st, {"merged": [{"number": 1}], "unmerged": [], "stale": [],
                                         "lead_in": [], "lead_sidecars": []})
    sel = mine_repo.load_selection(path, st)
    assert sel["merged"] == [{"number": 1}]
    mine_repo.update_selection(path, {"evidence": {"bob": "2025-01-02T00:00:00Z"}})
    assert mine_repo.load_selection(path, st)["evidence"] == {"bob": "2025-01-02T00:00:00Z"}
    st.max_prs = 200                                  # different settings: not reused
    assert mine_repo.load_selection(path, st) is None
    assert mine_repo.load_selection(str(tmp_path / "missing.json"), st) is None
