"""The historical CI map reaches past GitHub's 40,000-run list limit by reading older runs a week at a time."""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import github_api  # noqa: E402

FMT = "%Y-%m-%dT%H:%M:%SZ"


def _repo(n_days, per_day=3):
    """Synthetic workflow runs, newest first: per day push runs (alternating verdicts), a PR run, a cancelled run."""
    top = datetime(2026, 1, 1, tzinfo=timezone.utc)
    runs = []
    for d in range(n_days):
        t = top - timedelta(days=d)
        for k in range(per_day):
            ev, concl = [("push", "failure" if d % 2 else "success"), ("pull_request", "failure"), ("push", "cancelled")][k % 3]
            runs.append({"head_sha": f"s{d}_{k}", "event": ev, "name": "CI", "conclusion": concl,
                         "created_at": (t - timedelta(minutes=k)).strftime(FMT)})
    return runs


def _api(tmp_path, monkeypatch, runs, cap):
    st = config.Settings()
    st.ci_cache_dir = str(tmp_path)
    monkeypatch.setenv("RIFFLE_NO_API_CACHE", "1")
    api = github_api.GitHubAPI("o", "r", st)
    api.CI_PAGE_CAP = cap
    api.CI_FILTER_CAP = cap * 100 // 2                       # a filtered list holds fewer than the plain one, as on GitHub
    api.CI_EMPTY_WEEKS = 3
    asked = []

    def fake(path, params=None):
        import urllib.error
        asked.append(dict(params))
        pool = runs
        if "created" in params:
            lo, hi = params["created"].split("..")
            pool = [r for r in runs if lo <= r["created_at"] <= hi]
        page = params["page"]
        if "created" not in params and page > cap:
            raise urllib.error.HTTPError("u", 422, "pagination is limited", {}, None)
        window = pool[(page - 1) * 100: page * 100]
        if "created" in params and page * 100 > api.CI_FILTER_CAP:
            window = []                                          # a filtered list ends at its cap, with no error
        return 200, {"total_count": len(pool), "workflow_runs": window}
    monkeypatch.setattr(api, "_get_persistent", fake)
    return api, asked


def test_older_runs_come_from_date_windows(tmp_path, monkeypatch):
    runs = _repo(400, per_day=6)                                      # 2,400 runs; a plain list holds 200, a filtered one 100
    api, asked = _api(tmp_path, monkeypatch, runs, cap=2)
    ci = api.build_ci_conclusion_map(str(tmp_path / "m.json"))
    # ground truth: every non-PR run's verdict, none of it lost to the list limit
    want = {}
    for r in runs:
        if r["event"] != "pull_request" and r["conclusion"] in ("success", "failure"):
            want[r["head_sha"]] = r["conclusion"]
    assert ci == want and len(ci) == 800
    assert any("created" in p for p in asked)                         # the windows were used
    assert os.path.isfile(tmp_path / "m.json") and not os.path.isfile(tmp_path / "m.json.pages.json")


def test_a_repo_that_fits_one_list_does_not_use_windows(tmp_path, monkeypatch):
    runs = _repo(30)                                                  # 90 runs: one page
    api, asked = _api(tmp_path, monkeypatch, runs, cap=2)
    ci = api.build_ci_conclusion_map(str(tmp_path / "m.json"))
    assert len(ci) == 30 and not any("created" in p for p in asked)


def test_a_stopped_walk_resumes_where_it_was(tmp_path, monkeypatch):
    runs = _repo(400, per_day=6)
    api, asked = _api(tmp_path, monkeypatch, runs, cap=2)
    calls = {"n": 0}
    real = api._get_persistent

    def flaky(path, params=None):
        calls["n"] += 1
        return (500, None) if calls["n"] == 12 else real(path, params)
    monkeypatch.setattr(api, "_get_persistent", flaky)
    api.build_ci_conclusion_map(str(tmp_path / "m.json"))
    assert not os.path.isfile(tmp_path / "m.json") and os.path.isfile(tmp_path / "m.json.pages.json")
    monkeypatch.setattr(api, "_get_persistent", real)
    ci = api.build_ci_conclusion_map(str(tmp_path / "m.json"))
    assert len(ci) == 800 and os.path.isfile(tmp_path / "m.json")
