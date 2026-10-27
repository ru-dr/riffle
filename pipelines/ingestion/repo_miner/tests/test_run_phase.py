"""The web view's headline names what the run is doing, not just 'mining'."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tui  # noqa: E402

NOW = 10_000.0


def _repo(name, stage, **kw):
    r = tui.RepoStat(name, stage=stage)
    r.started = kw.pop("started", NOW - 100)
    for k, v in kw.items():
        setattr(r, k, v)
    return r


def phase(*repos, **kw):
    return tui.run_phase(repos=list(repos), now=NOW, **kw)


def test_mining_is_plain_mining():
    p = phase(_repo("o/a", "mining", last_event=NOW - 5))
    assert (p["word"], p["level"], p["detail"]) == ("MINING", "run", "")


def test_a_github_quota_wait_beats_mining():
    p = phase(_repo("o/a", "mining", last_event=NOW - 5, rl_until=NOW + 725))
    assert p["word"] == "RATE LIMITED" and p["level"] == "warn"
    assert p["detail"] == "GitHub quota used up: resumes in 12m05s"


def test_a_finished_wait_is_not_a_wait():
    assert phase(_repo("o/a", "mining", last_event=NOW - 5, rl_until=NOW - 1))["word"] == "MINING"


def test_priority_breaker_then_quota_then_llm_then_assembly():
    r = _repo("o/a", "mining", last_event=NOW - 5, rl_until=NOW + 60)
    assert phase(r, breaker="3 failures")["word"] == "STOPPED: ERROR"
    assert phase(r, llm_paused="until 14:00")["word"] == "RATE LIMITED"
    assert phase(_repo("o/a", "mining", last_event=NOW - 5), llm_paused="until 14:00")["word"] == "LLM PAUSED"
    assert phase(_repo("o/a", "mining", last_event=NOW - 5), assembling=True)["word"] == "ASSEMBLING"


def test_a_silent_mining_repo_is_no_progress():
    p = phase(_repo("o/a", "mining", last_event=NOW - tui.STALL_SECS - 60))
    assert p["word"] == "NO PROGRESS" and p["level"] == "warn" and "o/a" in p["detail"]


def test_the_current_stage_names_the_status():
    assert phase(_repo("o/a", "cloning"))["word"] == "CLONING"
    p = phase(_repo("o/a", "timelines"))
    assert p["word"] == "TIMELINES" and "o/a: timelines" in p["detail"]
    assert phase(_repo("o/a", "fetching PRs p12"))["word"] == "FETCHING PRS"      # the page number is dropped


def test_only_repos_in_flight_count():
    done = _repo("o/a", "done", ended=NOW - 5, rl_until=NOW + 600)
    nxt = _repo("o/b", "queued", started=None)
    assert phase(done, nxt)["word"] == "STARTING"
    assert phase(done)["word"] == "WAITING"
