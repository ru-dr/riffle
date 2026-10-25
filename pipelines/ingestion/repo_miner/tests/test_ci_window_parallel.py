"""A busy week's pages are read side by side and give the same runs as reading them one after the other."""
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import github_api  # noqa: E402


def _api(monkeypatch, total, fail_page=None):
    monkeypatch.setenv("RIFFLE_NO_API_CACHE", "1")
    st = config.Settings()
    st.github_token = "t"
    api = github_api.GitHubAPI("o", "r", st)
    asked, lock = [], threading.Lock()

    def page(params):
        n = params["page"]
        with lock:
            asked.append(n)
        if n == fail_page:
            return 0, None
        runs = [{"id": (n - 1) * 100 + i} for i in range(100) if (n - 1) * 100 + i < total]
        return 200, {"total_count": total, "workflow_runs": runs}
    monkeypatch.setattr(api, "_ci_page", page)
    return api, asked


def test_all_pages_are_read_once_and_fed_in_order(monkeypatch):
    api, asked = _api(monkeypatch, 350)
    got = []
    n = api._ci_window("2026-01-01T00:00:00Z", "2026-01-08T00:00:00Z", lambda runs: got.extend(r["id"] for r in runs))
    assert n == 350 and got == list(range(350))                # page order, none twice, none missing
    assert sorted(asked) == [1, 2, 3, 4]


def test_a_window_of_exactly_one_page_still_checks_for_a_second(monkeypatch):
    api, asked = _api(monkeypatch, 100)
    assert api._ci_window("2026-01-01T00:00:00Z", "2026-01-08T00:00:00Z", lambda runs: None) == 100
    assert sorted(asked) == [1, 2]


def test_one_failed_page_fails_the_window(monkeypatch):
    api, _ = _api(monkeypatch, 350, fail_page=3)
    assert api._ci_window("2026-01-01T00:00:00Z", "2026-01-08T00:00:00Z", lambda runs: None) is None
