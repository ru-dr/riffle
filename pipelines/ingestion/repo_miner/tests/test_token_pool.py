"""GITHUB_TOKENS: several tokens of one owner, used in turn when one has no quota left."""
import io
import json
import os
import sys
import time
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import github_api  # noqa: E402


def _api(tmp_path, monkeypatch, tokens, main=None):
    monkeypatch.setenv("RIFFLE_NO_API_CACHE", "1")
    st = config.Settings()
    st.ci_cache_dir = str(tmp_path)
    st.github_token = main
    st.github_tokens = tuple(tokens)
    return github_api.GitHubAPI("o", "r", st)


def _limit(reset_in=600):
    return urllib.error.HTTPError("u", 403, "x", {"X-RateLimit-Remaining": "0",
                                                  "X-RateLimit-Reset": str(int(time.time()) + reset_in)}, io.BytesIO(b""))


class _Ok:
    status = 200

    def __init__(self, headers=None):
        self.headers = headers or {}

    def read(self):
        return json.dumps({"ok": 1}).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _run(monkeypatch, steps, on_sleep=None):
    """urlopen answers from `steps` (an exception is raised, a headers dict is a 200); returns the tokens used and the sleeps.
    `on_sleep` runs when the code sleeps (time does not pass in a test, so it plays the part of the reset)."""
    used, slept = [], []

    def fake(req, timeout=None):
        used.append(req.get_header("Authorization"))
        step = steps.pop(0) if len(steps) > 1 else steps[0]
        if isinstance(step, Exception):
            raise step
        return _Ok(step)
    monkeypatch.setattr(github_api.urllib.request, "urlopen", fake)
    monkeypatch.setattr(github_api.time, "sleep", lambda s: (slept.append(s), on_sleep and on_sleep()))
    return used, slept


def test_settings_read_extra_tokens_from_the_environment(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKENS", "a1, b1\nc1 ,,")
    assert config.Settings().github_tokens == ("a1", "b1", "c1")
    monkeypatch.delenv("GITHUB_TOKENS")
    assert config.Settings().github_tokens == ()


def test_one_token_has_no_pool(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch, [], main="solo-token")
    assert api._pool is None and api.token == "solo-token"
    assert _api(tmp_path, monkeypatch, ["solo-token"], main="solo-token")._pool is None      # the same token twice is one


def test_extra_tokens_alone_work_and_the_main_token_comes_first(tmp_path, monkeypatch):
    assert _api(tmp_path, monkeypatch, ["x2", "x3"]).token == "x2"
    api = _api(tmp_path, monkeypatch, ["m2", "m3"], main="m1")
    assert api._pool.tokens == ["m1", "m2", "m3"] and api.token == "m1"


def test_a_spent_token_hands_over_to_the_next_without_waiting(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch, ["s2"], main="s1")
    used, slept = _run(monkeypatch, [_limit(), {}])
    assert api._get("/p")[:2] == (200, {"ok": 1})
    assert used == ["Bearer s1", "Bearer s2"] and slept == []
    used.clear()
    api._get("/p")                                             # s1 is known to be out: straight to s2
    assert used == ["Bearer s2"]


def test_a_response_with_no_requests_left_blocks_that_token_before_the_403(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch, ["p2"], main="p1")
    used, slept = _run(monkeypatch, [{"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(time.time()) + 600)}, {}])
    api._get("/p")
    api._get("/p")
    assert used == ["Bearer p1", "Bearer p2"]


def test_when_every_token_is_out_it_waits_for_the_first_to_come_back(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch, ["w2"], main="w1")
    used, slept = _run(monkeypatch, [_limit(900), _limit(300), {}], on_sleep=lambda: api._pool._until.clear())
    assert api._get("/p")[:2] == (200, {"ok": 1})
    assert used == ["Bearer w1", "Bearer w2", "Bearer w1"]      # the fake reset frees both: the first in order wins
    assert len(slept) == 1 and 295 <= slept[0] <= 310          # the sooner of the two resets
    assert os.path.isfile(tmp_path / ".rate_wait")


def test_graphql_and_rest_quotas_are_counted_apart(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch, ["g2"], main="g1")
    used, slept = _run(monkeypatch, [_limit(), {}, {}])
    api._get("/graphql", body={"query": "{}"})                 # g1 runs out of GraphQL points
    used.clear()
    api._get("/p")                                             # its REST quota is untouched
    assert used == ["Bearer g1"]


def test_the_persistent_getter_switches_too(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch, ["c2"], main="c1")
    used, slept = _run(monkeypatch, [_limit(), {}])
    assert api._get_persistent("/p") == (200, {"ok": 1})
    assert used == ["Bearer c1", "Bearer c2"] and slept == []


def test_tokens_are_never_printed(tmp_path, monkeypatch, capsys):
    api = _api(tmp_path, monkeypatch, ["secret-two"], main="secret-one")
    _run(monkeypatch, [_limit(), _limit(), {}], on_sleep=lambda: api._pool._until.clear())
    api._get("/p")
    out = capsys.readouterr()
    assert "secret-one" not in out.out + out.err and "secret-two" not in out.out + out.err
