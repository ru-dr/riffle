"""The structured run trace (runlog.py) and its report."""
from __future__ import annotations

import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import runlog  # noqa: E402
import trace_report  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_runlog():
    yield
    os.environ.pop("RIFFLE_TRACE", None)
    os.environ.pop("RIFFLE_LOG_LEVEL", None)
    runlog.configure()
    runlog.set_repo(None)


def _events(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh]


def test_trace_off_writes_nothing_and_costs_no_error(tmp_path, monkeypatch):
    monkeypatch.delenv("RIFFLE_TRACE", raising=False)
    runlog.configure()
    runlog._STATE["path"] = None
    with runlog.pr_scope(1):
        with runlog.span("git", cmd="log"):
            pass
    assert not list(tmp_path.iterdir())


def test_pr_scope_collects_stage_totals_and_levels(tmp_path, monkeypatch):
    path = str(tmp_path / "t.jsonl")
    runlog.configure(path=path, level="INFO")
    runlog.set_repo("o/r")
    with runlog.pr_scope(42):
        runlog.step("diff")
        with runlog.span("git", cmd="log"):
            time.sleep(0.01)
        runlog.step("labels")
    ev = _events(path)
    done = [e for e in ev if e["stage"] == "pr" and e["msg"] == "done"]
    assert len(done) == 1 and done[0]["pr"] == 42 and done[0]["repo"] == "o/r"
    assert done[0]["stages"]["git"]["n"] == 1 and "pr.diff" in done[0]["stages"]
    assert not [e for e in ev if e["lvl"] == "DEBUG"]          # INFO level hides per-call detail
    runlog.configure(path=path, level="DEBUG")
    with runlog.pr_scope(43):
        with runlog.span("git", cmd="log"):
            pass
    assert [e for e in _events(path) if e["lvl"] == "DEBUG" and e["stage"] == "git"]


def test_warn_echoes_to_stderr_and_failing_span_is_logged(tmp_path, monkeypatch, capsys):
    path = str(tmp_path / "t.jsonl")
    runlog.configure(path=path, level="INFO")
    runlog.warn("api", "server error; backing off", path="/x", code=502)
    assert "[warn] api: server error" in capsys.readouterr().err
    try:
        with runlog.span("git", "call"):
            raise ValueError("boom")
    except ValueError:
        pass
    assert [e for e in _events(path) if e["lvl"] == "ERROR" and e["exc"] == "ValueError"]


def test_stage_marks_print_duration_and_report_runs(tmp_path, capsys):
    path = str(tmp_path / "t.jsonl")
    runlog.configure(path=path, level="INFO")
    runlog._STAGE.update(name="szz", t0=time.monotonic() - 125)
    runlog.stage("mining PRs")
    runlog.stage(None)
    assert "[stage] szz done in 2m05s" in capsys.readouterr().err
    with runlog.pr_scope(7):
        runlog.step("diff")
    buf = io.StringIO()
    trace_report.report(trace_report.load(path), out=buf)
    text = buf.getvalue()
    assert "Stages" in text and "szz" in text and "#7" in text


def test_fields_named_like_parameters_do_not_collide(tmp_path):
    path = str(tmp_path / "t.jsonl")
    runlog.configure(path=path, level="INFO")
    runlog.info("batch", "start", level="INFO", stage="x", msg="m")      # caught a real crash in a live run
    assert _events(path)[0]["level"] == "INFO"
