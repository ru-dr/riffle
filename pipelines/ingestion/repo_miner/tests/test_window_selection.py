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
    assert p["llm_model"] == llm_flags.resolved_model(st)
    assert p["llm_prompt_version"] == llm_flags.prompt_version(st)
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


def test_cap_on_training_counts_only_training_prs():
    import scope
    prs = _prs()
    for p in prs:                                   # every other PR by a maintainer
        if p["number"] % 2:
            p["author_association"] = "MEMBER"
    st = _st(window_from="2025-01-01", window_to="2025-12-31", max_prs=30, lead_in_days=0,
             cap_on="training", llm_sample_pct=0)
    api = FakeAPI(prs, st)
    kept = api.window_prs() + api.unmerged
    train = [p for p in kept if scope.pr_list_is_training(p, "o/r", 0)]
    assert len(train) == 30 and len(kept) > 30      # maintainer PRs are mined too
    st.cap_on = "all"
    assert len(FakeAPI(prs, st).window_prs() + FakeAPI(prs, st).unmerged) <= 60


def test_full_repo_window_keeps_every_eligible_pr():
    st = _st(window_from=config.FULL_REPO_FROM, window_to=config.FULL_REPO_TO, lead_in_days=0)
    api = FakeAPI(_prs(), st)
    kept = api.window_prs() + api.unmerged
    assert len(kept) == sum(1 for p in _prs() if not p["draft"])
    assert not api.lead_in


# -- legacy mode (no window): the unmerged rows come from the merged PRs' own time span -----------------------

def test_legacy_unmerged_rows_are_closed_inside_the_span_of_the_kept_merged_prs():
    """A cap of 20 merged PRs used to leave the unmerged PRs unbounded, so a repo with 20 merges in three weeks came out
    with unmerged rows from the months around them (real runs: 130 of 168 closed before the oldest merge)."""
    st = _st(max_prs=20, enable_merge_outcome=True)
    api = FakeAPI(_prs(), st)
    merged = api.merged_prs()
    lo, hi = min(p["merged_at"] for p in merged), max(p["merged_at"] for p in merged)
    assert len(merged) == 20 and api.merged_span == (lo, hi)
    assert api.unmerged, "the span holds some unmerged PRs"
    assert all(lo <= p["closed_at"] <= hi for p in api.unmerged), "an unmerged PR closed outside the merged span was kept"
    mix = len(merged) / (len(merged) + len(api.unmerged))
    assert 0.5 < mix < 0.8, f"the merged share should be near the repo's real two thirds, got {mix:.2f}"


def test_a_commit_with_over_a_hundred_check_runs_is_read_not_crashed():
    """The check-runs loop used names that belong to the CI-history loop (pages_path, raw), so any commit with a second
    page of check runs raised NameError and the PR was dropped from the dataset."""
    st = _st()

    class Fake(GitHubAPI):
        def __init__(self):
            super().__init__("o", "r", st)

        def _get(self, path, params=None, body=None):
            if path.endswith("/check-runs"):
                page = (params or {}).get("page", 1)
                n = 100 if page == 1 else 30
                return 200, {"check_runs": [{"conclusion": "success", "name": f"job-{page}-{i}", "id": page * 1000 + i,
                                             "completed_at": "2026-01-01T00:00:00Z"} for i in range(n)]}, {}
            return 200, {"statuses": []}, {}
    out = Fake().check_conclusion("abc")
    assert out["required"] == "success"


def test_health_flags_prs_dropped_by_errors():
    """Two PRs of a real run were dropped by a NameError and the health file said nothing; a skip caused by an error is now
    a flag (a PR with no diff is not)."""
    import health
    rows = [{"repo": "o/r", "label_merged": True, "merged_at": "2026-01-01T00:00:00Z"}] * 60
    ok = health.check(rows, skipped={"no-rows (no diff / missing commit / shallow clone)": 3})
    assert not any("dropped" in f for f in ok["flags"]) and ok["stats"]["skipped_errors"] == 0
    bad = health.check(rows, skipped={"exc:NameError": 2, "no-rows (no diff / missing commit / shallow clone)": 1})
    assert any("2 PRs dropped by errors" in f and "NameError" in f for f in bad["flags"]) and bad["stats"]["skipped_errors"] == 2
