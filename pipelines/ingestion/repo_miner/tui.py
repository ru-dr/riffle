"""
tui.py — terminal UI for build_dataset.py.

Configure a batch (repos file, output, PR cap, workers, LLM/SZZ/CI toggles),
start it, and watch it live: per-repo status, overall progress + ETA, LLM
success/failure and latency, circuit-breaker state, and the raw log.

    python tui.py                    # defaults: repos.txt -> dataset.parquet

The batch runs as a normal `build_dataset.py` subprocess; progress comes from
structured events it writes to a temp file (RIFFLE_EVENTS, see progress.py), so
the TUI never parses log text. Stop sends SIGINT; the miner's checkpoint makes
a later Start (with "reuse clones" on) resume instead of redoing PRs.

Keys: ctrl+r start · ctrl+x stop · ctrl+l clear log · ctrl+q quit
Missing Python dependencies (textual, pandas, pyarrow, lizard, semgrep) are
installed on first launch by deps.py; RIFFLE_NO_INSTALL=1 skips that.
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import tempfile
import time
from collections import deque
from dataclasses import dataclass, field

import deps

deps.ensure()                     # install missing dependencies before importing textual

from rich.text import Text  # noqa: E402
from textual import on  # noqa: E402
from textual.app import App, ComposeResult  # noqa: E402
from textual.binding import Binding  # noqa: E402
from textual.containers import Horizontal, Vertical, VerticalScroll  # noqa: E402
from textual.screen import ModalScreen  # noqa: E402
from textual.widgets import (Button, Checkbox, DataTable, Footer, Header, Input,  # noqa: E402
                             Label, ProgressBar, RichLog, Select, Static)

MODELS = [("Sonnet (default)", "sonnet"), ("Opus", "opus"), ("Haiku (fast/cheap)", "haiku"),
          ("Fable 5.1", "claude-fable-5-1")]

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(HERE, "build_dataset.py")
TEST_OUT = os.path.join(HERE, "test_output", "test_dataset.jsonl")


def _fmt_secs(s: float | None) -> str:
    if s is None or s != s or s < 0:
        return "—"
    s = int(s)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}h{m:02d}m" if h else (f"{m}m{sec:02d}s" if m else f"{sec}s")


@dataclass
class RepoStat:
    name: str
    stage: str = "queued"
    total: int | None = None
    done: int = 0
    resumed: int = 0
    rows: int = 0
    skips: int = 0
    llm_ok: int = 0
    llm_fail: int = 0
    started: float | None = None
    ended: float | None = None


@dataclass
class RunStats:
    started: float | None = None
    ended: float | None = None
    pr_times: deque = field(default_factory=lambda: deque(maxlen=200))  # wall-clock stamps
    llm_secs: deque = field(default_factory=lambda: deque(maxlen=200))
    breaker: str | None = None
    batch_ok: int | None = None


class DataViewer(ModalScreen):
    """Shows the rows a test run saved: one line per column, one column per PR.
    `p` toggles between the JSONL file and its parquet copy (with column types)."""
    BINDINGS = [Binding("escape,q", "app.pop_screen", "Close"),
                Binding("p", "toggle", "JSONL / Parquet")]
    DEFAULT_CSS = """
    DataViewer { align: center middle; }
    #viewer { width: 95%; height: 90%; border: round $accent; background: $surface; }
    #viewer_title { height: 2; padding: 0 1; }
    #viewer_table { height: 1fr; }
    """

    def __init__(self, path: str):
        super().__init__()
        self.path = path

    def compose(self) -> ComposeResult:
        with Vertical(id="viewer"):
            yield Static("", id="viewer_title")
            yield DataTable(id="viewer_table", zebra_stripes=True)

    def on_mount(self) -> None:
        self._load()

    def _other(self) -> str:
        base, ext = os.path.splitext(self.path)
        return base + (".parquet" if ext == ".jsonl" else ".jsonl")

    def action_toggle(self) -> None:
        other = self._other()
        if not os.path.isfile(other):
            self.app.notify(f"Not found: {other}", severity="warning")
            return
        self.path = other
        self._load()

    def _read(self) -> tuple[list[dict], dict[str, str]]:
        """Rows plus {column: stored type} (types only for parquet)."""
        if self.path.endswith(".parquet"):
            import pyarrow.parquet as pq
            table = pq.read_table(self.path)
            types = {f.name: str(f.type) for f in table.schema}
            return table.to_pylist(), types
        with open(self.path, encoding="utf-8") as fh:
            return [json.loads(l) for l in fh if l.strip()], {}

    def _load(self) -> None:
        t = self.query_one("#viewer_table", DataTable)
        t.clear(columns=True)
        try:
            rows, types = self._read()
        except Exception as e:                                # noqa: BLE001
            self.query_one("#viewer_title", Static).update(
                f"[b]{self.path}[/b]\n[red]could not read: {type(e).__name__}: {e}[/]")
            return
        fmt = "Parquet" if self.path.endswith(".parquet") else "JSONL"
        other = "Parquet" if fmt == "JSONL" else "JSONL"
        self.query_one("#viewer_title", Static).update(
            f"[b]{fmt}[/b]  {self.path}\n{len(rows)} rows x {len(rows[0]) if rows else 0} "
            f"columns · p: show {other} · esc: close")
        t.add_column("column", key="col")
        if types:
            t.add_column("type", key="type")
        for r in rows:
            t.add_column(f"PR #{r.get('pr_number')}", key=f"pr{r.get('pr_number')}")
        for k in (list(rows[0]) if rows else []):
            vals = [Text("null", style="dim") if r.get(k) is None else str(r.get(k))[:60]
                    for r in rows]
            t.add_row(k, *([Text(types[k], style="cyan")] if types else []), *vals)


class RiffleTUI(App):
    TITLE = "Riffle miner"
    SUB_TITLE = "build_dataset"
    CSS = """
    #form { width: 42; border: round $primary; padding: 0 1; }
    #form Input { margin-bottom: 0; }
    #form Label { color: $text-muted; margin-top: 1; }
    #form Checkbox { margin-top: 1; }
    #buttons { height: 3; margin-top: 1; }
    #buttons Button { width: 1fr; }
    #main { padding: 0 1; }
    #stats { height: 6; border: round $secondary; padding: 0 1; }
    #overall { height: 1; margin: 0 0 1 0; }
    #repos { height: 1fr; min-height: 6; border: round $secondary; }
    #log { height: 1fr; min-height: 6; border: round $secondary; }
    .breaker { color: $error; text-style: bold; }
    """
    BINDINGS = [
        Binding("ctrl+r", "start", "Start"),
        Binding("ctrl+x", "stop", "Stop"),
        Binding("ctrl+t", "test_llm", "Test LLM"),
        Binding("ctrl+e", "test_run", "Test run"),
        Binding("ctrl+o", "view_test", "View test data"),
        Binding("ctrl+g", "show_split", "Show split"),
        Binding("ctrl+l", "clear_log", "Clear log"),
        Binding("ctrl+q", "quit", "Quit"),
    ]

    def __init__(self):
        super().__init__()
        self.proc: asyncio.subprocess.Process | None = None
        self.events_path: str | None = None
        self.events_pos = 0
        self.repos: dict[str, RepoStat] = {}
        self.stats = RunStats()
        self.max_prs: int | None = None
        self.test_mode = False
        self.out_paths: list[str] = []

    # ---------------------------------------------------------------- layout
    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with VerticalScroll(id="form"):
                yield Label("Repos file")
                yield Input("repos.txt", id="repos_file", compact=True)
                yield Label("Output dataset")
                yield Input("dataset.parquet", id="out", compact=True)
                yield Label("Number of PCs (1 = mine everything here)")
                yield Input("1", id="pcs", type="integer", compact=True)
                yield Label("This PC # (1..PCs)")
                yield Input("1", id="pc_index", type="integer", compact=True)
                yield Label("Clone dir")
                yield Input("cloned_repos", id="clone_dir", compact=True)
                yield Label("Max PRs per repo (blank = all)")
                yield Input("100", id="max_prs", type="integer", compact=True)
                yield Label("Skip PRs merged in last N days (mature labels)")
                yield Input("90", id="min_age", type="integer", compact=True)
                yield Label("PR workers")
                yield Input(str(min(6, os.cpu_count() or 2)), id="workers", type="integer", compact=True)
                yield Label("LLM concurrency")
                yield Input("4", id="llm_conc", type="integer", compact=True)
                yield Label("Test repo (Test run only)")
                yield Input("https://github.com/pallets/flask", id="test_repo", compact=True)
                yield Label("Test PRs")
                yield Input("10", id="test_prs", type="integer", compact=True)
                yield Label("LLM model")
                yield Select(MODELS, value="sonnet", allow_blank=False, id="llm_model", compact=True)
                yield Checkbox("LLM flags (claude -p)", True, id="llm", compact=True)
                yield Checkbox("SZZ label", True, id="szz", compact=True)
                yield Checkbox("CI history", False, id="ci_history", compact=True)
                yield Checkbox("Scanners (lizard/semgrep)", True, id="scanners", compact=True)
                yield Checkbox("API contract diff", False, id="api_contract", compact=True)
                yield Checkbox("Git PR discovery", False, id="git_discovery", compact=True)
                yield Checkbox("Reuse clones (resume)", True, id="skip_existing", compact=True)
                yield Checkbox("Keep clones", False, id="keep_clones", compact=True)
                with Horizontal(id="buttons"):
                    yield Button("Show split", id="split", variant="default")
                    yield Button("Test LLM", id="test", variant="primary")
                    yield Button("Test run", id="testrun", variant="warning")
                    yield Button("Start", id="start", variant="success")
                    yield Button("Stop", id="stop", variant="error", disabled=True)
            with Vertical(id="main"):
                yield Static("Idle. Configure on the left, then Start (ctrl+r).", id="stats")
                yield ProgressBar(id="overall", show_eta=False)
                yield DataTable(id="repos", zebra_stripes=True, cursor_type="row")
                yield RichLog(id="log", max_lines=5000, wrap=False, highlight=False)
        yield Footer()

    def on_mount(self) -> None:
        t = self.query_one("#repos", DataTable)
        for key, label in [("repo", "Repo"), ("stage", "Stage"), ("prs", "PRs"),
                           ("rows", "Rows"), ("skips", "Skips"), ("llm", "LLM ok/fail"),
                           ("time", "Time")]:
            t.add_column(label, key=key)
        self.set_interval(0.25, self._poll_events)
        self.set_interval(1.0, self._render_stats)

    # ---------------------------------------------------------------- actions
    def _val(self, wid: str) -> str:
        return self.query_one(f"#{wid}", Input).value.strip()

    def _on(self, wid: str) -> bool:
        return self.query_one(f"#{wid}", Checkbox).value

    def _build_cmd(self) -> list[str]:
        cmd = [sys.executable, "-u", BUILD,
               "--repos-file", self._val("repos_file") or "repos.txt",
               "--out", self._val("out") or "dataset.parquet",
               "--clone-dir", self._val("clone_dir") or "cloned_repos"]
        if self._val("max_prs"):
            cmd += ["--max-prs", self._val("max_prs")]
        if self._val("min_age") and self._val("min_age") != "0":
            cmd += ["--min-age-days", self._val("min_age")]
        shard = self._shard()
        if shard:
            cmd += ["--shard", shard]
        if self._val("workers"):
            cmd += ["--workers", self._val("workers")]
        if self._val("llm_conc"):
            cmd += ["--llm-concurrency", self._val("llm_conc")]
        cmd += ["--llm-model", self._model()]
        for wid, flag in [("llm", "--llm"), ("szz", "--szz"), ("ci_history", "--ci-history"),
                          ("skip_existing", "--skip-existing"), ("keep_clones", "--keep-clones")]:
            if self._on(wid):
                cmd.append(flag)
        if not self._on("scanners"):
            cmd.append("--no-scanners")
        if not self._on("api_contract"):
            cmd.append("--no-api-contract")
        if self._on("git_discovery"):
            cmd += ["--pr-discovery", "git"]
        return cmd

    def _output_paths(self, test: bool) -> list[str]:
        """Absolute file(s) this run will save to."""
        if test:
            return [TEST_OUT, TEST_OUT.rsplit(".", 1)[0] + ".parquet"]
        out = self._val("out") or "dataset.parquet"
        out = out if os.path.isabs(out) else os.path.join(HERE, out)
        if out.endswith(".parquet"):
            import importlib.util
            if not (importlib.util.find_spec("pandas") and importlib.util.find_spec("pyarrow")):
                out = out.rsplit(".", 1)[0] + ".jsonl"      # miner's fallback
        return [out]

    def _shard(self) -> str | None:
        """"K/N" for this PC, or None when mining everything on one machine."""
        pcs, k = self._val("pcs"), self._val("pc_index")
        n = int(pcs) if pcs.isdigit() else 1
        i = int(k) if k.isdigit() else 1
        return f"{i}/{n}" if n > 1 else None

    def _shard_ok(self) -> bool:
        pcs, k = self._val("pcs"), self._val("pc_index")
        if not pcs.isdigit() or int(pcs) < 1:
            self.notify("Number of PCs must be 1 or more.", severity="error")
            return False
        if int(pcs) > 1 and (not k.isdigit() or not 1 <= int(k) <= int(pcs)):
            self.notify(f"This PC # must be between 1 and {pcs}.", severity="error")
            return False
        return True

    @on(Button.Pressed, "#split")
    def _split_btn(self) -> None:
        self.action_show_split()

    def action_show_split(self) -> None:
        """Print how the repos file splits over the chosen number of PCs."""
        if not self._shard_ok():
            return
        self.run_worker(self._run_split(), group="split")

    async def _run_split(self) -> None:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, BUILD, "--repos-file", self._val("repos_file") or "repos.txt",
            "--shard-plan", self._val("pcs") or "1", cwd=HERE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await proc.communicate()
        me = self._val("pc_index") or "1"
        for line in out.decode("utf-8", "replace").splitlines():
            mine = line.strip().startswith(f"PC {me}/")
            self._log(line, style="bold green" if mine else None)

    def _model(self) -> str:
        return str(self.query_one("#llm_model", Select).value)

    @on(Button.Pressed, "#test")
    def _test_btn(self) -> None:
        self.action_test_llm()

    def action_test_llm(self) -> None:
        if self.proc is not None:
            self.notify("Stop the batch first.", severity="warning")
            return
        self._log(f"[test] one headless LLM call with model={self._model()} ...", style="bold")
        self.query_one("#test", Button).disabled = True
        self.run_worker(self._run_test(), group="test")

    async def _run_test(self) -> None:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, os.path.join(HERE, "mine_repo.py"), "--llm-test",
            "--llm-model", self._model(), cwd=HERE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await proc.communicate()
        msg = out.decode("utf-8", "replace").strip().splitlines()[-1:] or ["(no output)"]
        ok = proc.returncode == 0
        self._log(f"[test] {msg[0]}", style="bold green" if ok else "bold red")
        self.notify("LLM test passed" if ok else "LLM test failed",
                    severity="information" if ok else "error")
        self.query_one("#test", Button).disabled = False

    @on(Button.Pressed, "#testrun")
    def _testrun_btn(self) -> None:
        self.action_test_run()

    def action_test_run(self) -> None:
        """Mine a few real PRs from the first repo into test_output/ and show them."""
        self.action_start(test=True)

    def action_view_test(self) -> None:
        if os.path.isfile(TEST_OUT):
            self.push_screen(DataViewer(TEST_OUT))
        else:
            self.notify("No test data yet; run Test run (ctrl+e) first.", severity="warning")

    @on(Button.Pressed, "#start")
    def _start_btn(self) -> None:
        self.action_start()

    @on(Button.Pressed, "#stop")
    def _stop_btn(self) -> None:
        self.action_stop()

    def action_start(self, test: bool = False) -> None:
        if self.proc is not None:
            self.notify("A run is already in progress.", severity="warning")
            return
        if not test and not self._shard_ok():
            return
        repos_file = self._val("repos_file") or "repos.txt"
        if not test and not os.path.isfile(os.path.join(HERE, repos_file)) and not os.path.isfile(repos_file):
            self.notify(f"Repos file not found: {repos_file}", severity="error")
            return
        mp = self._val("max_prs")
        self.max_prs = int(mp) if mp.isdigit() else None
        self.test_mode = test
        if test:
            tp = self._val("test_prs")
            self.max_prs = int(tp) if tp.isdigit() else 10
        self.repos.clear()
        self.stats = RunStats(started=time.time())
        self.query_one("#repos", DataTable).clear()
        fd, self.events_path = tempfile.mkstemp(prefix="riffle-events-", suffix=".jsonl")
        os.close(fd)
        self.events_pos = 0
        self.out_paths = self._output_paths(test)
        cmd = self._build_cmd()
        if test:
            cmd += ["--test", "--test-prs", self._val("test_prs") or "10", "--test-repo",
                    self._val("test_repo") or "https://github.com/pallets/flask"]
        self._log(f"$ {' '.join(cmd[2:])}", style="bold")
        self._log(f"[tui] saving to: {'  +  '.join(self.out_paths)}", style="cyan")
        self.run_worker(self._run_proc(cmd), exclusive=True, group="proc")
        self._set_running(True)

    def action_stop(self) -> None:
        if self.proc is None:
            return
        try:
            os.killpg(self.proc.pid, signal.SIGINT)
            self._log("[stop] SIGINT sent; the checkpoint keeps finished PRs. "
                      "Start again with 'Reuse clones' on to resume.", style="yellow")
        except ProcessLookupError:
            pass

    def action_clear_log(self) -> None:
        self.query_one("#log", RichLog).clear()

    async def action_quit(self) -> None:
        if self.proc is not None:
            self.action_stop()
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=10)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        self.exit()

    def _set_running(self, running: bool) -> None:
        self.query_one("#start", Button).disabled = running
        self.query_one("#stop", Button).disabled = not running
        self.query_one("#test", Button).disabled = running
        self.query_one("#testrun", Button).disabled = running
        self.query_one("#split", Button).disabled = running
        for w in self.query("#form Input, #form Checkbox, #form Select"):
            w.disabled = running

    # ------------------------------------------------------------- subprocess
    async def _run_proc(self, cmd: list[str]) -> None:
        env = dict(os.environ, RIFFLE_EVENTS=self.events_path, PYTHONUNBUFFERED="1")
        self.proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=HERE, env=env, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            start_new_session=True)
        assert self.proc.stdout is not None
        while True:
            line = await self.proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", "replace").rstrip()
            if text.startswith("[pr "):
                continue          # per-PR lines are shown in the table instead
            style = ("bold red" if "breaker" in text or "FAILED" in text or "[error" in text
                     else "yellow" if text.startswith(("[warn", "[skip")) else None)
            self._log(text, style=style)
        rc = await self.proc.wait()
        self.proc = None
        self._poll_events()
        self.stats.ended = time.time()
        self._render_stats()
        self._set_running(False)
        msg = {0: "finished", 3: "stopped by LLM circuit breaker"}.get(rc, f"exited with code {rc}")
        if rc < 0 or rc == 130:
            msg = "stopped"
        self._log(f"[tui] batch {msg} after {_fmt_secs(self.stats.ended - self.stats.started)}",
                  style="bold green" if rc == 0 else "bold red")
        self.notify(f"Batch {msg}", severity="information" if rc == 0 else "error")
        for path in self.out_paths:
            if os.path.isfile(path):
                self._log(f"[tui] saved: {path} ({self._row_count(path)} rows, "
                          f"{os.path.getsize(path) / 1024:.0f} KB)", style="bold cyan")
            else:
                self._log(f"[tui] not written: {path}", style="yellow")
        if self.test_mode and rc == 0 and os.path.isfile(TEST_OUT):
            self._log(f"[test] saved -> {TEST_OUT} (+ .parquet)  ctrl+o to view, p to switch",
                      style="bold green")
            self.push_screen(DataViewer(TEST_OUT))

    @staticmethod
    def _row_count(path: str) -> int | str:
        try:
            if path.endswith(".parquet"):
                import pyarrow.parquet as pq
                return pq.ParquetFile(path).metadata.num_rows
            with open(path, encoding="utf-8") as fh:
                return sum(1 for line in fh if line.strip())
        except Exception:                                   # noqa: BLE001
            return "?"

    def _log(self, text: str, style: str | None = None) -> None:
        self.query_one("#log", RichLog).write(Text(text, style=style or ""))

    # ----------------------------------------------------------------- events
    def _poll_events(self) -> None:
        if not self.events_path:
            return
        try:
            with open(self.events_path, encoding="utf-8") as fh:
                fh.seek(self.events_pos)
                chunk = fh.read()
                # only consume complete lines
                cut = chunk.rfind("\n") + 1
                self.events_pos += len(chunk[:cut].encode("utf-8"))
        except OSError:
            return
        for line in chunk[:cut].splitlines():
            try:
                self._apply(json.loads(line))
            except (json.JSONDecodeError, KeyError, TypeError):
                continue

    def _repo(self, name: str) -> RepoStat:
        if name not in self.repos:
            self.repos[name] = RepoStat(name)
            self.query_one("#repos", DataTable).add_row(name, "queued", "—", "0", "0", "—", "—",
                                                        key=name)
        return self.repos[name]

    def _apply(self, e: dict) -> None:
        ev, t = e.get("ev"), e.get("t", time.time())
        if ev == "batch_start":
            for name in e.get("repos", []):
                self._repo(name)
        elif ev == "repo_queue":
            r = self._repo(e["repo"])
            r.stage = "cloning" if e.get("stage") == "clone" else "starting"
        elif ev == "repo_start":
            r = self._repo(e["repo"])
            r.stage, r.total, r.resumed, r.started = "mining", e.get("total"), e.get("resumed", 0), t
            r.done = r.resumed
        elif ev == "pr":
            r = self._repo(e["repo"])
            r.done += 1
            self.stats.pr_times.append(t)
            if e.get("status") == "skip":
                r.skips += 1
            else:
                r.rows += 1
                if e.get("llm") == "ok":
                    r.llm_ok += 1
                    if e.get("llm_secs"):
                        self.stats.llm_secs.append(e["llm_secs"])
                elif e.get("llm") == "fail":
                    r.llm_fail += 1
        elif ev == "breaker":
            r = self._repo(e["repo"])
            r.stage = "BREAKER"
            self.stats.breaker = f"tripped on {e['repo']} PR #{e.get('num')}: {e.get('msg')}"
        elif ev == "repo_done":
            r = self._repo(e["repo"])
            r.ended = t
            r.rows = e.get("rows", r.rows)
            r.stage = "done" if e.get("status") == "ok" else e.get("status", "done")
        elif ev == "repo_failed":
            r = self._repo(e["repo"])
            r.stage, r.ended = f"failed ({e.get('stage')})", t
        elif ev == "batch_done":
            self.stats.batch_ok = e.get("ok")
        if "repo" in e:
            self._update_row(self.repos[e["repo"]])

    def _update_row(self, r: RepoStat) -> None:
        t = self.query_one("#repos", DataTable)
        prs = f"{r.done}/{r.total}" if r.total is not None else "—"
        llm = f"{r.llm_ok}/{r.llm_fail}" if (r.llm_ok or r.llm_fail) else "—"
        el = None
        if r.started:
            el = (r.ended or time.time()) - r.started
        style = "bold red" if r.stage == "BREAKER" or r.stage.startswith("failed") \
            else "green" if r.stage == "done" else ""
        stage = Text(r.stage, style=style)
        for col, val in [("stage", stage), ("prs", prs), ("rows", str(r.rows)),
                         ("skips", str(r.skips)), ("llm", llm), ("time", _fmt_secs(el))]:
            try:
                t.update_cell(r.name, col, val)
            except Exception:                         # row not yet rendered
                pass

    # ------------------------------------------------------------------ stats
    def _render_stats(self) -> None:
        if self.stats.started is None:
            return
        now = self.stats.ended or time.time()
        elapsed = now - self.stats.started
        done = sum(r.done for r in self.repos.values())
        # expected total: known per-repo totals, else the PR cap as an estimate
        total = 0
        unknown = False
        for r in self.repos.values():
            if r.total is not None:
                total += r.total
            elif r.stage.startswith("failed"):
                continue
            elif self.max_prs:
                total += self.max_prs
            else:
                unknown = True
        # throughput over the recent window (robust to slow pre-passes)
        rate = None
        pts = self.stats.pr_times
        if len(pts) >= 2 and pts[-1] > pts[0]:
            rate = (len(pts) - 1) / (pts[-1] - pts[0])            # PRs / s
        eta = (total - done) / rate if rate and total and not unknown else None
        llm_ok = sum(r.llm_ok for r in self.repos.values())
        llm_fail = sum(r.llm_fail for r in self.repos.values())
        lat = (sum(self.stats.llm_secs) / len(self.stats.llm_secs)) if self.stats.llm_secs else None
        repos_done = sum(1 for r in self.repos.values() if r.ended)
        state = "running" if self.proc else "finished"
        parts = [f"[b]{state}[/b]", f"elapsed {_fmt_secs(elapsed)}",
                 f"repos {repos_done}/{len(self.repos)}",
                 f"PRs {done}/{total if total and not unknown else '?'}"]
        if rate:
            parts.append(f"{rate * 60:.1f} PR/min")
        if self.proc and eta:
            parts.append(f"ETA {_fmt_secs(eta)}")
        line1 = "  ·  ".join(parts)
        line2 = (f"LLM ok {llm_ok}  fail {llm_fail}  ·  avg latency "
                 f"{f'{lat:.1f}s' if lat else '—'}  ·  breaker "
                 + (f"[bold red]{self.stats.breaker}[/]" if self.stats.breaker else "[green]armed[/]"))
        line3 = ("[b]Saving to:[/b] " if self.proc else "[b]Saved to:[/b] ") + \
            "\n            ".join(self.out_paths)
        self.query_one("#stats", Static).update(line1 + "\n" + line2 + "\n" + line3)
        bar = self.query_one("#overall", ProgressBar)
        if total and not unknown:
            bar.update(total=total, progress=done)
        for r in self.repos.values():
            if r.stage == "mining":
                self._update_row(r)


if __name__ == "__main__":
    RiffleTUI().run()
