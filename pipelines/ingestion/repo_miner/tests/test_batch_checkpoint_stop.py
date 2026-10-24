"""The batch driver's reads of the shared checkpoint, its stop and skip handling, and the clone watchdog."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import build_dataset as bd  # noqa: E402


def _row(repo, n, pad=0):
    return json.dumps({"repo": repo, "pr_number": n, "text": "x" * pad}) + "\n"


# ---- _checkpointed (M15) ------------------------------------------------------------

def test_checkpointed_counts_without_parsing_whole_rows(tmp_path, monkeypatch):
    f = tmp_path / "d.jsonl.partial.jsonl"
    f.write_text(_row("O/A", 1) + _row("o/b", 2) + _row("o/a", 3))
    calls = []
    real = json.loads
    monkeypatch.setattr(bd.json, "loads", lambda *a, **k: (calls.append(a), real(*a, **k))[1])
    assert bd._checkpointed(str(f)) == {"o/a": 2, "o/b": 1}
    assert calls == []                                       # the regex on the leading field was enough
    with open(f, "a") as fh:
        fh.write('{"repo": "o/c')                            # a last line cut by a crash is not a row
    assert bd._checkpointed(str(f)) == {"o/a": 2, "o/b": 1}


def test_checkpointed_falls_back_to_json_when_repo_is_not_first(tmp_path):
    f = tmp_path / "c.jsonl"
    f.write_text('{"pr_number": 1, "repo": "o/z"}\n' + '{"repo": "o/e\\u0073c"}\n')
    assert bd._checkpointed(str(f)) == {"o/z": 1, "o/esc": 1}


def test_checkpointed_reads_only_the_appended_lines(tmp_path, monkeypatch):
    f = tmp_path / "c.jsonl"
    f.write_text(_row("o/a", 1, 5000) * 50)
    assert bd._checkpointed(str(f)) == {"o/a": 50}
    seen = []
    real = bd._line_repo
    monkeypatch.setattr(bd, "_line_repo", lambda l: (seen.append(l), real(l))[1])
    assert bd._checkpointed(str(f)) == {"o/a": 50} and seen == []          # unchanged (size, mtime): no read at all
    with open(f, "a") as fh:
        fh.write(_row("o/b", 1) * 3)
    assert bd._checkpointed(str(f)) == {"o/a": 50, "o/b": 3}
    assert len(seen) == 3                                    # only the new lines were looked at


def test_checkpointed_rereads_after_a_rewrite(tmp_path):
    f = tmp_path / "c.jsonl"
    f.write_text(_row("o/a", 1) * 10)
    assert bd._checkpointed(str(f)) == {"o/a": 10}
    f.write_text(_row("o/b", 1) * 10 + _row("o/b", 2))        # rewritten larger, different start
    assert bd._checkpointed(str(f)) == {"o/b": 11}
    f.write_text(_row("o/c", 1))                              # and smaller
    assert bd._checkpointed(str(f)) == {"o/c": 1}
    os.remove(f)
    assert bd._checkpointed(str(f)) == {}


def test_a_finished_last_line_without_newline_counts_but_is_looked_at_again(tmp_path):
    f = tmp_path / "c.jsonl"
    f.write_text(_row("o/a", 1) + '{"repo": "o/a"}')
    assert bd._checkpointed(str(f)) == {"o/a": 2}
    with open(f, "a") as fh:
        fh.write("\n")
    assert bd._checkpointed(str(f)) == {"o/a": 2}


# ---- _drop_repo (H1/H2 interplay, M6 reader) ------------------------------------------

def test_drop_repo_keeps_other_repos_and_tolerates_torn_lines(tmp_path):
    pytest.importorskip("pandas")   # build_dataset imports pandas; CI's core job has none
    out = tmp_path / "d.jsonl"
    ckpt = tmp_path / "d.jsonl.partial.jsonl"
    ckpt.write_text(_row("o/keep", 1) + _row("O/Drop", 1) + "{not json at all\n" + '{"repo": "o/drop", "x": \n'
                    + _row("o/drop", 2) + "\n" + _row("o/keep", 2))
    bd._drop_repo(str(out), "o/drop", cache_dir=str(tmp_path / "cache"))
    lines = ckpt.read_text().splitlines()
    assert [json.loads(l)["pr_number"] for l in lines if l.startswith('{"repo": "o/keep"')] == [1, 2]
    assert not any('"o/drop"' in l.lower() for l in lines)        # the torn line that named the repo went too
    assert "{not json at all" in lines                            # a damaged line of unknown repo stays
    assert not (tmp_path / "d.jsonl.partial.jsonl.tmp").exists()
    assert bd._checkpointed(str(ckpt)) == {"o/keep": 2}


def test_status_after_a_drop_rewrite_is_not_stale(tmp_path):
    pytest.importorskip("pandas")
    ckpt = tmp_path / "d.jsonl.partial.jsonl"
    ckpt.write_text(_row("o/a", 1) * 5 + _row("o/b", 1) * 5)
    assert bd._checkpointed(str(ckpt)) == {"o/a": 5, "o/b": 5}
    bd._drop_repo(str(tmp_path / "d.jsonl"), "o/a", cache_dir=str(tmp_path))
    assert bd._checkpointed(str(ckpt)) == {"o/b": 5}


# ---- the quota-wait file and the selection read the run's cache dir ------------------

def test_status_reads_the_rate_wait_file_in_the_ci_cache_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cache = tmp_path / "elsewhere"
    cache.mkdir()
    (cache / ".rate_wait").write_text(str(int(time.time()) + 600))
    args = types.SimpleNamespace(out="d.parquet", shard="1/2", split_id="x", ci_cache_dir=str(cache))
    monkeypatch.setattr(bd, "_SYNC", {"args": args, "repos": {}, "counts": {}})
    monkeypatch.setattr(bd, "_run_settings", lambda a: {})
    assert bd._sync_status("heartbeat")["rate_wait_until"]


# ---- skip control (L38) ---------------------------------------------------------------

def test_a_transiently_empty_skip_file_is_read_again(tmp_path, monkeypatch):
    ctl = tmp_path / "skip.txt"
    ctl.write_text("")
    monkeypatch.setattr(bd, "_CONTROL", str(ctl))
    real_sleep = time.sleep

    def refill(_s):                                           # the writer finishes between the two reads
        ctl.write_text("o/a\n")
        real_sleep(0)
    monkeypatch.setattr(bd.time, "sleep", refill)
    assert bd._skip_requested("O/A") is True
    ctl.write_text("")
    monkeypatch.setattr(bd.time, "sleep", lambda s: None)
    assert bd._skip_requested("o/a") is False
    monkeypatch.setattr(bd, "_CONTROL", str(tmp_path / "missing"))
    assert bd._skip_requested("o/a") is False


# ---- stopping the miner (M37, M38) ----------------------------------------------------

POSIX = pytest.mark.skipif(os.name == "nt", reason="process groups")


@POSIX
def test_a_stop_reaches_the_miners_children():
    code = ("import subprocess, sys, time; "
            "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
            "print(c.pid, flush=True); time.sleep(60)")
    proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True, start_new_session=True)
    child = int(proc.stdout.readline())
    bd._stop_child(proc, signal)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            return
        try:
            if open(f"/proc/{child}/stat").read().split()[2] == "Z":    # dead, not yet reaped by init
                return
        except OSError:
            return
        time.sleep(0.1)
    raise AssertionError("the grandchild outlived the stop")


@POSIX
def test_a_second_stop_while_stopping_is_passed_on(monkeypatch):
    proc = subprocess.Popen([sys.executable, "-c",
                             "import signal, time; signal.signal(signal.SIGINT, signal.SIG_IGN); time.sleep(60)"],
                            start_new_session=True)
    time.sleep(0.5)
    real_wait = proc.wait
    state = {"n": 0}

    def wait(timeout=None):
        state["n"] += 1
        if state["n"] == 1:
            raise KeyboardInterrupt                           # the second Stop, pressed while waiting
        return real_wait(timeout=timeout)
    monkeypatch.setattr(proc, "wait", wait)
    with pytest.raises(KeyboardInterrupt):
        bd._stop_child(proc, signal)
    assert proc.poll() is not None                            # and the miner is gone


@POSIX
def test_a_stop_during_a_skip_is_not_swallowed(tmp_path, monkeypatch):
    ctl = tmp_path / "skip.txt"
    ctl.write_text("o/a\n")
    monkeypatch.setattr(bd, "_CONTROL", str(ctl))
    monkeypatch.setattr(bd, "_sync_beat", lambda *a, **k: None)

    def boom(proc, sig):
        proc.kill()
        proc.wait()
        raise KeyboardInterrupt
    monkeypatch.setattr(bd, "_stop_child", boom)
    with pytest.raises(KeyboardInterrupt):
        bd._run_watched([sys.executable, "-c", "import time; time.sleep(60)"], "o/a")


@POSIX
def test_the_miner_runs_in_its_own_group_and_a_skip_stops_it(tmp_path, monkeypatch):
    ctl = tmp_path / "skip.txt"
    ctl.write_text("o/a\n")
    monkeypatch.setattr(bd, "_CONTROL", str(ctl))
    monkeypatch.setattr(bd, "_sync_beat", lambda *a, **k: None)
    t0 = time.monotonic()
    rc, skipped = bd._run_watched([sys.executable, "-c", "import time; time.sleep(60)"], "o/a")
    assert skipped and time.monotonic() - t0 < 20


# ---- the clone watchdog -----------------------------------------------------------------

@POSIX
def test_a_silent_clone_is_killed_after_the_stall_limit(monkeypatch):
    monkeypatch.setattr(bd, "_CONTROL", None)
    monkeypatch.setattr(bd, "_sync_beat", lambda *a, **k: None)
    t0 = time.monotonic()
    rc, skipped = bd._run_watched([sys.executable, "-c", "import time; time.sleep(60)"], "o/a", stall_s=2)
    assert rc == -9 and not skipped and time.monotonic() - t0 < 20


@POSIX
def test_a_clone_that_keeps_printing_is_not_stalled(monkeypatch):
    monkeypatch.setattr(bd, "_CONTROL", None)
    monkeypatch.setattr(bd, "_sync_beat", lambda *a, **k: None)
    code = "import sys, time\nfor _ in range(5):\n    sys.stderr.write('Receiving objects\\r'); sys.stderr.flush(); time.sleep(1)"
    rc, skipped = bd._run_watched([sys.executable, "-c", code], "o/a", stall_s=3)
    assert rc == 0 and not skipped


def test_clone_stall_follows_the_environment(monkeypatch):
    monkeypatch.delenv("RIFFLE_CLONE_STALL_S", raising=False)
    assert bd._clone_stall_s() == 600.0
    monkeypatch.setenv("RIFFLE_CLONE_STALL_S", "45")
    assert bd._clone_stall_s() == 45.0
    monkeypatch.setenv("RIFFLE_CLONE_STALL_S", "0")
    assert bd._clone_stall_s() is None


# ---- re-mine keeps the prompt and model with each LLM answer (LLM wiring) ---------------

def test_saved_llm_answers_carry_the_prompt_version_and_model(tmp_path):
    pd = pytest.importorskip("pandas")
    from features import llm_flags
    flag = next(iter(llm_flags.FLAG_SCHEMA))
    df = pd.DataFrame([{"base_sha": "b", "head_sha": "h", flag: 3, "llm_prompt_version": "abc123", "llm_model": "sonnet"},
                       {"base_sha": "b2", "head_sha": "h2", flag: 2, "llm_prompt_version": None, "llm_model": None}])
    path = tmp_path / "x.llm_reuse.json"
    bd._save_llm_reuse(df, str(path), "o/r")
    recs = json.loads(path.read_text())
    assert recs[0]["prompt_version"] == "abc123" and recs[0]["model"] == "sonnet"
    assert "prompt_version" not in recs[1] and "model" not in recs[1]


def test_settings_have_the_new_knobs(monkeypatch):
    import config
    monkeypatch.delenv("RIFFLE_MAX_FILES_DEEP", raising=False)
    monkeypatch.delenv("LLM_DIFF_CONTEXT", raising=False)
    st = config.Settings()
    assert st.max_files_per_pr_deep == 300 and st.llm_diff_context == 1
    assert not hasattr(st, "llm_breaker_threshold")
    monkeypatch.setenv("RIFFLE_MAX_FILES_DEEP", "0")
    monkeypatch.setenv("LLM_DIFF_CONTEXT", "4")
    st = config.Settings()
    assert st.max_files_per_pr_deep == 0 and st.llm_diff_context == 4


def test_child_settings_are_exported_to_the_environment(monkeypatch):
    for k in ("LLM_BACKEND", "LLM_DIFF_CONTEXT", "RIFFLE_MAX_FILES_DEEP", "RIFFLE_CLONE_STALL_S"):
        monkeypatch.delenv(k, raising=False)
    args = bd.build_parser().parse_args(["--max-files-deep", "50", "--llm-backend", "api",
                                         "--llm-diff-context", "2", "--clone-stall-s", "90"])
    bd._export_child_settings(args)
    assert (os.environ["RIFFLE_MAX_FILES_DEEP"], os.environ["LLM_BACKEND"], os.environ["LLM_DIFF_CONTEXT"],
            os.environ["RIFFLE_CLONE_STALL_S"]) == ("50", "api", "2", "90")
    for k in ("LLM_BACKEND", "LLM_DIFF_CONTEXT", "RIFFLE_MAX_FILES_DEEP", "RIFFLE_CLONE_STALL_S"):
        os.environ.pop(k, None)


def test_checkpointed_notices_a_rewrite_with_the_same_size_and_mtime(tmp_path):
    f = tmp_path / "c.jsonl"
    f.write_text(_row("o/a", 1) * 10)
    assert bd._checkpointed(str(f)) == {"o/a": 10}
    st = os.stat(f)
    f.write_text(_row("o/b", 1) * 10)                          # same length ("o/a" -> "o/b")
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns))           # and the same mtime
    assert os.stat(f).st_size == st.st_size and os.stat(f).st_mtime_ns == st.st_mtime_ns
    assert bd._checkpointed(str(f)) == {"o/b": 10}
