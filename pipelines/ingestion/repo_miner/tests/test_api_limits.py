"""Rate limits, partial pagination and cache lifetimes in github_api / api_cache (audit v6.13.3)."""
import email.utils
import io
import json
import os
import sys
import threading
import time
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api_cache  # noqa: E402
import config  # noqa: E402
import github_api  # noqa: E402


def _api(tmp_path, monkeypatch, disk=False):
    if disk:
        monkeypatch.delenv("RIFFLE_NO_API_CACHE", raising=False)
    else:
        monkeypatch.setenv("RIFFLE_NO_API_CACHE", "1")
    st = config.Settings()
    st.ci_cache_dir = str(tmp_path)
    st.github_token = "t"
    return github_api.GitHubAPI("o", "r", st)


def _err(code, headers=None, body=b""):
    return urllib.error.HTTPError("u", code, "x", headers or {}, io.BytesIO(body))


class _Ok:
    status = 200
    headers = {}

    def __init__(self, data):
        self.data = data

    def read(self):
        return json.dumps(self.data).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _script(monkeypatch, steps):
    """urlopen answers from `steps` in order: an exception is raised, anything else is returned as a body."""
    calls = []

    def fake(req, timeout=None):
        calls.append(req.full_url)
        step = steps.pop(0) if len(steps) > 1 else steps[0]
        if isinstance(step, Exception):
            raise step
        return _Ok(step)
    monkeypatch.setattr(github_api.urllib.request, "urlopen", fake)
    slept = []
    monkeypatch.setattr(github_api.time, "sleep", lambda s: slept.append(s))
    return calls, slept


# -- H11, M10 ---------------------------------------------------------------
def test_persistent_get_returns_a_plain_403(tmp_path, monkeypatch):          # Actions disabled, SSO: not a rate limit
    calls, slept = _script(monkeypatch, [_err(403, body=b'{"message": "Actions is disabled"}')])
    assert _api(tmp_path, monkeypatch)._get_persistent("/p") == (403, None)
    assert len(calls) == 1 and slept == []


def test_persistent_get_waits_for_a_primary_reset(tmp_path, monkeypatch):
    reset = str(int(time.time()) + 30)
    calls, slept = _script(monkeypatch, [_err(403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": reset}),
                                         {"ok": 1}])
    assert _api(tmp_path, monkeypatch)._get_persistent("/p") == (200, {"ok": 1})
    assert 25 <= slept[0] <= 40
    assert os.path.isfile(tmp_path / ".rate_wait")                           # the wait note lives under ci_cache_dir


def test_persistent_get_caps_a_secondary_limit(tmp_path, monkeypatch):
    calls, slept = _script(monkeypatch, [_err(403, {"Retry-After": "2"})])
    assert _api(tmp_path, monkeypatch)._get_persistent("/p") == (403, None)
    assert len(calls) == github_api.MAX_SECONDARY_WAITS + 1


def test_a_secondary_limit_message_without_retry_after_is_waited_out(tmp_path, monkeypatch):
    msg = b'{"message": "You have exceeded a secondary rate limit."}'
    calls, slept = _script(monkeypatch, [_err(403, body=msg), {"ok": 1}])
    assert _api(tmp_path, monkeypatch)._get("/p")[:2] == (200, {"ok": 1})
    assert slept == [60.0]


def test_retry_after_may_be_an_http_date(tmp_path, monkeypatch):
    date = email.utils.formatdate(time.time() + 20, usegmt=True)
    calls, slept = _script(monkeypatch, [_err(429, {"Retry-After": date}), {"ok": 1}])
    assert _api(tmp_path, monkeypatch)._get("/p")[:2] == (200, {"ok": 1})
    assert 15 <= slept[0] <= 25


def test_a_bare_429_backs_off_a_bounded_number_of_times(tmp_path, monkeypatch):
    calls, slept = _script(monkeypatch, [_err(429)])
    assert _api(tmp_path, monkeypatch)._get("/p")[:2] == (429, None)
    assert len(calls) == github_api.MAX_NO_SIGNAL_429 + 1
    assert slept == sorted(slept) and max(slept) <= 60


def test_a_rate_wait_holds_every_thread(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(github_api.time, "sleep", lambda s: seen.append(s))
    github_api._GATE_UNTIL = time.time() + 50                  # another thread is waiting out a limit
    try:
        github_api._gate_wait()
    finally:
        github_api._GATE_UNTIL = 0.0
    assert seen and 45 <= seen[0] <= 50
    seen.clear()
    github_api._gate_wait()
    assert seen == []                                          # no wait, no sleep


# -- M1 ---------------------------------------------------------------------
def test_review_pages_cut_short_are_unknown_and_not_cached(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch, disk=True)
    full = [{"submitted_at": "2026-01-01T00:00:00Z", "state": "APPROVED", "user": {"login": "a"}}] * 100

    def get(path, params=None, body=None):
        if path.endswith("/reviews") and params["page"] == 2:
            return 500, None, {}
        return 200, list(full), {}
    monkeypatch.setattr(api, "_get", get)
    assert api.review_events(5) is None
    assert api._disk.get("rv:5:1") is None

    monkeypatch.setattr(api, "_get", lambda path, params=None, body=None: (200, list(full), {}))   # always full: 10-page cap
    assert api.review_events(5) is None
    assert api._disk.get("rv:5:1") is None


# -- M11 --------------------------------------------------------------------
def test_a_4xx_on_the_runs_list_keeps_the_checkpoint(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    runs = [{"head_sha": f"s{i}", "event": "push", "name": "CI", "conclusion": "success",
             "created_at": "2026-01-01T00:00:00Z"} for i in range(100)]

    def get(path, params=None):
        if params["page"] == 11:
            raise _err(401)
        return 200, {"workflow_runs": runs}
    monkeypatch.setattr(api, "_get_persistent", get)
    cache = str(tmp_path / "o_r.json")
    assert len(api.build_ci_conclusion_map(cache)) == 100
    assert not os.path.isfile(cache)
    with open(cache + ".pages.json") as fh:
        assert json.load(fh)["next_page"] == 11


# -- M2 ---------------------------------------------------------------------
def test_ci_map_cache_is_versioned_and_expires(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    path = str(tmp_path / "m.json")
    built = []

    def get(p, params=None):
        built.append(1)
        return 200, {"workflow_runs": [{"head_sha": "a", "event": "push", "name": "CI",
                                        "conclusion": "failure", "created_at": "2026-01-01T00:00:00Z"}]}
    monkeypatch.setattr(api, "_get_persistent", get)

    with open(path, "w") as fh:
        json.dump({"old_sha": "success"}, fh)                    # the pre-6.13.0 bare map: discarded
    assert api.build_ci_conclusion_map(path) == {"a": "failure"} and len(built) == 1
    assert api.build_ci_conclusion_map(path) == {"a": "failure"} and len(built) == 1     # now a hit

    assert api.build_ci_conclusion_map(path, covers_until="2999-01-01T00:00:00Z") and len(built) == 2
    d = json.load(open(path))
    d["built"] -= api.CI_MAP_TTL_S + 10
    json.dump(d, open(path, "w"))
    api.build_ci_conclusion_map(path)
    assert len(built) == 3                                       # past its TTL: rebuilt


def test_an_unfinished_rebuild_returns_the_older_whole_map(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    path = str(tmp_path / "m.json")
    json.dump({"v": api.CI_MAP_VERSION, "built": time.time() - api.CI_MAP_TTL_S - 5, "map": {"x": "success"}},
              open(path, "w"))
    monkeypatch.setattr(api, "_get_persistent", lambda p, params=None: (500, None))
    assert api.build_ci_conclusion_map(path) == {"x": "success"}


# -- L23 --------------------------------------------------------------------
def test_a_window_that_cannot_be_split_is_unread_not_truncated(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    monkeypatch.setattr(api, "_get_persistent",
                        lambda p, params=None: (200, {"total_count": 5000, "workflow_runs": [{}] * 100}))
    got = []
    assert api._ci_window("2026-01-01T00:00:00Z", "2026-01-01T00:00:01Z", got.extend) is None
    assert got == []


# -- M12 --------------------------------------------------------------------
def test_a_failed_checks_page_gives_no_verdict(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    api.token = None                                             # REST path
    run = {"name": "build", "conclusion": "success", "completed_at": "t", "id": 1}

    def get(path, params=None, body=None):
        if path.endswith("/check-runs"):
            return (200, {"check_runs": [run] * 100}, {}) if params["page"] == 1 else (500, None, {})
        return 200, {"statuses": []}, {}
    monkeypatch.setattr(api, "_get", get)
    assert api.check_conclusion("abc") == {"required": None, "optional": None, "checks": []}


def test_legacy_statuses_are_paged_and_a_failure_is_unknown(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    st = [{"state": "success", "context": f"ci{i}"} for i in range(100)]
    pages = {1: (200, {"total_count": 130, "statuses": st}, {}),
             2: (200, {"total_count": 130, "statuses": st[:30]}, {})}
    monkeypatch.setattr(api, "_get", lambda path, params=None, body=None: pages[params["page"]])
    assert len(api._statuses_rest("abc")) == 130
    pages[2] = (502, None, {})
    assert api._statuses_rest("abc") is None


def test_suites_at_the_page_cap_are_unreadable(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    runs = [{"check_suite_id": 1, "event": "push"}] * 100
    monkeypatch.setattr(api, "_get", lambda path, params=None, body=None: (200, {"workflow_runs": runs}, {}))
    assert api._suites_for_events("abc", ("push",)) is None
    assert api._check_runs_rest("abc", ("push",)) is None


def test_labels_at_with_a_failed_page_is_unknown(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    ev = [{"event": "labeled", "created_at": "2026-01-01T00:00:00Z", "label": {"name": "Bug"}}] * 100
    monkeypatch.setattr(api, "_get", lambda path, params=None, body=None:
                        (200, ev, {}) if params["page"] == 1 else (500, None, {}))
    assert api.labels_at(1, "2026-02-01T00:00:00Z") is None
    monkeypatch.setattr(api, "_get", lambda path, params=None, body=None: (200, ev, {}))      # 20 full pages
    assert api.labels_at(1, "2026-02-01T00:00:00Z") is None
    monkeypatch.setattr(api, "_get", lambda path, params=None, body=None: (200, ev[:3], {}))
    assert api.labels_at(1, "2026-02-01T00:00:00Z") == {"bug"}


def test_force_pushes_with_more_than_a_page_are_unknown(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)

    def gql(q, strict=False):
        assert "hasNextPage" in q
        return {"repository": {"p1": {"timelineItems": {"pageInfo": {"hasNextPage": True}, "nodes": []}},
                               "p2": {"timelineItems": {"pageInfo": {"hasNextPage": False},
                                                        "nodes": [{"createdAt": "t", "beforeCommit": {"oid": "x"}}]}}}}
    monkeypatch.setattr(api, "_gql", gql)
    assert api.force_pushes([1, 2]) == {1: None, 2: [("t", "x")]}


# -- L27 --------------------------------------------------------------------
def test_gql_block_never_moves_back(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    api._gql_blocked_until = time.time() + 1000
    monkeypatch.setattr(api, "_get", lambda *a, **k: (200, {"errors": [{"type": "RATE_LIMITED"}]}, {}))
    monkeypatch.setattr(api, "_gql_reset_time", lambda: time.time() + 10)
    assert api._gql("q") is None
    assert api._gql_blocked_until > time.time() + 900


# -- L29, L34 ---------------------------------------------------------------
def test_an_open_prs_timeline_is_remembered_briefly(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch, disk=True)
    monkeypatch.setattr(api, "_get", lambda p, params=None, body=None: (200, [{"event": "commented"}], {}))
    api.pr_timeline(1)
    monkeypatch.setattr(api, "_get", lambda p, params=None, body=None: (200, [{"event": "commented"}, {"event": "closed"}], {}))
    api.pr_timeline(2)
    assert api._disk.get("tl:1") is not None and api._disk.get("tl:2") is not None
    monkeypatch.setattr(api_cache.time, "time", lambda t=time.time(): t + api.OPEN_TIMELINE_TTL_S + 60)
    assert api._disk.get("tl:1") is None                          # open: expired after the short life
    assert api._disk.get("tl:2") is not None                      # closed: kept the long one


def test_expired_rows_are_purged_on_open(tmp_path, monkeypatch):
    path = str(tmp_path / "x.sqlite")
    c = api_cache.ApiCache(path)
    c.put("old", 1)
    c.put("new", 2)
    c._db.execute("UPDATE kv SET t = ? WHERE k = 'old'", (time.time() - api_cache.MAX_AGE_S - 5,))
    c._db.commit()
    c._db.close()
    d = api_cache.ApiCache(path)
    d.get("new")
    assert [r[0] for r in d._db.execute("SELECT k FROM kv")] == ["new"]


# -- L35, L89 ---------------------------------------------------------------
def test_user_created_at_is_read_once_across_threads(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    calls = []
    gate = threading.Event()

    def get(path, params=None, body=None):
        calls.append(path)
        gate.wait(2)
        return 200, {"created_at": "2020-01-01T00:00:00Z"}, {}
    monkeypatch.setattr(api, "_get", get)
    out = []
    ts = [threading.Thread(target=lambda: out.append(api.user_created_at("bob"))) for _ in range(6)]
    for t in ts:
        t.start()
    time.sleep(0.2)
    gate.set()
    for t in ts:
        t.join()
    assert out == ["2020-01-01T00:00:00Z"] * 6 and len(calls) == 1
