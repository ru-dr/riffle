"""The TUI survives text that looks like markup, reads the events file by bytes, writes the skip file whole,
quits without blocking its key handler and degrades the QR screen on a small terminal."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time

import pytest

os.environ.setdefault("RIFFLE_NO_DOCTOR", "1")
os.environ.setdefault("RIFFLE_NO_PLAN_USAGE", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("textual")
from textual.widgets import Static  # noqa: E402

import progress  # noqa: E402
import tui  # noqa: E402

EVIL = "own[/bold]er/re[po] [red]x"


def _run(fn, size=(160, 60)):
    async def go():
        app = tui.RiffleTUI()
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            return await fn(app, pilot)
    return asyncio.run(go())


def test_markup_looking_text_never_crashes():
    async def fn(app, pilot):
        app._apply({"ev": "repo_queue", "repo": EVIL, "stage": "clone", "t": time.time()})
        app.out_paths = [f"/tmp/{EVIL}.jsonl"]
        app.log_path = f"/tmp/{EVIL}.log"
        app.stats.started = time.time()
        app.stats.breaker = EVIL
        app._render_stats()
        app.notify(f"{EVIL} failed: [/]")
        await pilot.pause()
        app.query_one("#stats", Static)
        return True
    assert _run(fn)


def test_events_file_is_read_by_bytes_with_crlf_and_partial_line(tmp_path):
    async def fn(app, pilot):
        p = tmp_path / "ev.jsonl"
        app.events_path, app.events_pos = str(p), 0
        a = json.dumps({"ev": "repo_queue", "repo": "a/é", "t": 1}).encode()
        b = json.dumps({"ev": "repo_queue", "repo": "b/b", "t": 2}).encode()
        p.write_bytes(a + b"\r\n" + b[:10])             # CRLF, then a half-written line
        app._poll_events()
        assert set(app.repos) == {"a/é"}
        p.write_bytes(a + b"\r\n" + b + b"\r\n")
        app._poll_events()
        app._poll_events()                                # nothing new: nothing replayed
        assert set(app.repos) == {"a/é", "b/b"} and app.events_pos == len(a) + len(b) + 4
        return True
    assert _run(fn)


def test_event_writer_never_writes_crlf(tmp_path, monkeypatch):
    p = tmp_path / "w.jsonl"
    monkeypatch.setattr(progress, "_PATH", str(p))
    monkeypatch.setattr(progress, "_FH", None)
    progress.emit("x", n=1)
    progress._FH.close()
    monkeypatch.setattr(progress, "_FH", None)
    assert p.read_bytes().endswith(b"}\n") and b"\r" not in p.read_bytes()


def test_skip_file_is_replaced_whole(tmp_path):
    async def fn(app, pilot):
        app._apply({"ev": "repo_queue", "repo": "o/r", "t": time.time()})
        app.repos["o/r"].stage = "queued"
        app.control_path = str(tmp_path / "control.txt")
        app.proc = object()
        app.skip_set = set()
        t = app.query_one("#repos")
        t.move_cursor(row=0)
        app.action_skip_repo()
        assert open(app.control_path).read() == "o/r\n" and not os.path.exists(app.control_path + ".tmp")
        app.proc = None
        return True
    assert _run(fn)


def test_second_quit_does_not_block_and_forces_exit():
    class P:
        pid = 2 ** 22 + 1
        async def wait(self):
            await asyncio.sleep(3600)

    async def fn(app, pilot):
        killed = []
        orig = tui._signal_group
        tui._signal_group = lambda pid, kill=False: killed.append(kill)
        try:
            app.proc = P()
            await asyncio.wait_for(app.action_quit(), timeout=2)     # returns at once, the wait is in a worker
            assert app._quitting
            await asyncio.wait_for(app.action_quit(), timeout=2)
            assert True in killed
        finally:
            tui._signal_group = orig
            app.proc = None
        return True
    assert _run(fn)


def test_second_quit_kills_a_real_miner_that_ignores_stop():
    """Real process that ignores SIGINT: the first ctrl+q waits in a worker, the second kills it and quits,
    and no coroutine is left unawaited."""
    import warnings

    async def fn(app, pilot):
        app.proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c",
            "import signal, time; signal.signal(signal.SIGINT, signal.SIG_IGN); print('up', flush=True); time.sleep(60)",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, **tui._NEW_GROUP)
        proc = app.proc
        await asyncio.wait_for(proc.stdout.readline(), timeout=10)     # the handler is installed
        await asyncio.wait_for(app.action_quit(), timeout=2)
        await pilot.pause(0.3)
        assert proc.returncode is None and app._quitting      # still waiting, not blocked
        await asyncio.wait_for(app.action_quit(), timeout=2)
        await asyncio.wait_for(proc.wait(), timeout=5)
        assert proc.returncode is not None
        app.proc = None
        return True
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        assert _run(fn)


def test_qr_screen_degrades_on_a_small_terminal():
    pytest.importorskip("segno")

    async def fn(app, pilot):
        app.push_screen(tui.QRScreen("http://192.168.1.50:8765/?key=abcdefghijkl", "http://127.0.0.1:8765/?key=x"))
        await pilot.pause()
        texts = [str(w.render()) for w in app.screen.query(Static)]
        assert any("too small" in t for t in texts)
        assert any("192.168.1.50" in t for t in texts)
        return True
    assert _run(fn, size=(60, 20))


def test_data_viewer_survives_duplicate_pr_numbers(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text("\n".join(json.dumps({"pr_number": 7, "x": "[/bad]"}) for _ in range(2)) + "\n")

    async def fn(app, pilot):
        app.push_screen(tui.DataViewer(str(path)))
        await pilot.pause()
        return True
    assert _run(fn)
