"""stages.py must list the stages mine_repo.py really runs, and fold a trace into per-stage status."""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import stages  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_stage_list_matches_mine_repo():
    src = open(os.path.join(HERE, "mine_repo.py"), encoding="utf-8").read()
    called = re.findall(r'runlog\.stage\("([^"]+)"', src)
    assert list(stages.STAGE_NAMES) == called


def _ev(msg, name, repo="o/r", **kw):
    return {"stage": "stage", "msg": msg, "name": name, "repo": repo, "lvl": "INFO", **kw}


def test_report_status_and_timing():
    rep = stages.stage_report([
        _ev("start", "szz"), _ev("done", "szz", dur_s=4.0),
        _ev("start", "szz", repo="o/s"), _ev("done", "szz", repo="o/s", dur_s=9.5),
        _ev("start", "mining PRs"),
    ])
    assert rep["szz"].status() == "done" and rep["szz"].dur_s == 13.5 and rep["szz"].slowest == ("o/s", 9.5)
    assert rep["mining PRs"].status() == "running"
    assert rep["timelines"].status() == "pending"


def test_error_inside_a_stage_marks_it_failed():
    rep = stages.stage_report([
        _ev("start", "timelines"),
        {"stage": "api", "lvl": "ERROR", "msg": "boom", "repo": "o/r"},
    ])
    assert rep["timelines"].status() == "failed"


def test_remined_repo_counts_once_and_torn_lines_are_skipped():
    lines = ['{"stage": "stage", "msg": "start", "name": "szz", "repo": "o/r"}', '{"torn', "[1]",
             '{"stage": "stage", "msg": "done", "name": "szz", "repo": "o/r", "dur_s": 1}',
             '{"stage": "stage", "msg": "start", "name": "szz", "repo": "o/r"}']
    rep = stages.stage_report(stages.read_events(lines))
    assert rep["szz"].started == {"o/r"} and rep["szz"].finished == set() and rep["szz"].status() == "running"


def test_pr_steps_match_mine_repo():
    src = open(os.path.join(HERE, "mine_repo.py"), encoding="utf-8").read()
    called = re.findall(r'\b_step\("([^"]+)"', src)
    assert list(stages.PR_STEP_NAMES) == [c for c in called if c != "labels_done"]


def _t(sec):
    return f"2026-10-27T03:50:{sec:02d}.000Z"


def _trace():
    return [
        {"t": _t(0), "lvl": "INFO", "stage": "batch", "msg": "start"},
        {"t": _t(1), "lvl": "INFO", "stage": "batch", "msg": "repos", "repos": ["o/a", "o/b"]},
        {"t": _t(2), "lvl": "INFO", "stage": "repo", "msg": "start", "repo": "o/a", "workers": 6},
        {"t": _t(3), "lvl": "INFO", "stage": "stage", "msg": "start", "repo": "o/a", "name": "mining PRs",
         "total": 3, "todo": 3, "resumed": 0},
        {"t": _t(4), "lvl": "INFO", "stage": "pr", "msg": "done", "repo": "o/a", "pr": 1,
         "stages": {"pr.diff": {"n": 1, "s": 0.5}, "pr.llm": {"n": 1, "s": 4.0}, "llm": {"n": 1, "s": 3.9},
                    "git": {"n": 9, "s": 1.0}}},
        {"t": _t(5), "lvl": "INFO", "stage": "heartbeat", "msg": "alive", "repo": "o/a",
         "in_flight": [{"pr": 2, "stage": "pr.llm", "for_s": 3}, {"pr": 3, "stage": "pr.llm", "for_s": 1},
                       {"pr": 4, "stage": "pr.scanners", "for_s": 0}]},
    ]


def test_repo_view_counts_steps_llm_and_workers_in_flight():
    v = stages.repo_view(_trace(), "o/a")
    assert (v.prs_done, v.prs_total, v.workers) == (1, 3, 6)
    assert v.steps["diff"].n == 1 and v.steps["diff"].secs == 0.5 and v.steps["scanners"].n == 0
    assert v.steps["llm"].in_flight == 2 and v.steps["scanners"].in_flight == 1
    assert v.llm_calls == 1 and v.llm_secs == 3.9          # the "llm" span, not the "pr.llm" step
    assert not v.repo_done and v.stage("mining PRs").status() == "running"
    assert stages.repo_view(_trace(), "o/b").prs_done == 0   # a repo with no events is pending, not an error


def test_finished_repo_has_nobody_in_flight_and_a_remine_starts_over():
    ev = _trace() + [{"t": _t(6), "stage": "repo", "msg": "done", "repo": "o/a"}]
    v = stages.repo_view(ev, "o/a")
    assert v.repo_done and v.in_flight == []
    again = ev + [{"t": _t(7), "stage": "repo", "msg": "start", "repo": "o/a", "workers": 2}]
    v = stages.repo_view(again, "o/a")
    assert not v.repo_done and v.prs_done == 0 and v.steps["diff"].n == 0 and v.workers == 2


def test_run_state_plan_end_and_newer_run_replaces_older():
    st = stages.run_state(_trace())
    assert st["repos"] == ["o/a", "o/b"] and not st["batch_done"]
    ended = _trace() + [{"t": _t(9), "stage": "batch", "msg": "done", "aborted": True}]
    assert stages.run_state(ended)["batch_done"] and stages.run_state(ended)["aborted"]
    newer = ended + [{"t": _t(10), "stage": "batch", "msg": "start"},
                     {"t": _t(11), "stage": "repo", "msg": "start", "repo": "o/z"}]
    st = stages.run_state(newer)
    assert st["repos"] == ["o/z"] and not st["batch_done"]


def test_long_stages_report_progress():
    ev = _trace() + [{"t": _t(7), "stage": "stage", "msg": "progress", "repo": "o/a", "name": "open snapshots",
                      "phase": "force-pushes", "done": 500, "total": 43160, "secs_per_call": 9.1, "eta_s": 5400}]
    p = stages.repo_view(ev, "o/a").progress["open snapshots"]
    assert (p["phase"], p["done"], p["total"], p["eta_s"]) == ("force-pushes", 500, 43160, 5400)


def test_a_retried_stage_forgets_the_error_of_its_earlier_try():
    ev = [_ev("start", "timelines"), {"stage": "api", "lvl": "ERROR", "msg": "boom", "repo": "o/r"},
          _ev("start", "timelines"), _ev("done", "timelines", dur_s=3.0)]       # the second try finished cleanly
    assert stages.stage_report(ev)["timelines"].status() == "done"
    other = ev[:2] + [_ev("start", "timelines", repo="o/s"), _ev("done", "timelines", repo="o/s", dur_s=1)]
    assert stages.stage_report(other)["timelines"].status() == "failed"          # o/r's error still counts
    assert stages.stage_report(other, only="o/s")["timelines"].status() == "done"


def test_a_foreign_trace_line_cannot_break_the_report():
    ev = [{"stage": "stage", "msg": "start", "repo": ["not", "a", "string"], "name": "szz"},
          {"stage": "stage", "msg": "start", "repo": "o/r", "name": {"x": 1}}, _ev("start", "szz")]
    assert stages.stage_report(ev)["szz"].status() == "running"
