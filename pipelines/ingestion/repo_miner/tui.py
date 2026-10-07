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

Also shows live RAM/CPU of the whole miner process tree, names what killed a
run (e.g. out of memory), counts down GitHub rate-limit waits, flags repos with
no progress, saves every run's log under logs/, and remembers the form between
launches (.tui_settings.json).

Keys: ctrl+r start · ctrl+x stop · ctrl+l clear log · ctrl+y copy log ·
ctrl+b web view QR code (o open here, c copy link) · ctrl+q quit. Drag over the log or stats to
select text, then ctrl+c to copy it.
Every launch first runs doctor.py, which checks git, the Python packages,
GITHUB_TOKEN and the LLM setup and offers to fix them (install packages, save a
token to .env). RIFFLE_NO_DOCTOR=1 skips it; RIFFLE_NO_INSTALL=1 never installs.
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import tempfile
import time
import webbrowser
from collections import deque
from dataclasses import dataclass, field

import deps
from version import __version__

# Environment doctor on every launch, before the UI takes over the terminal:
# checks git / packages / GITHUB_TOKEN / LLM and offers to fix them. Skip with
# RIFFLE_NO_DOCTOR=1 (then only missing Python packages are auto-installed).
deps.use_project_venv()           # re-run inside repo_miner/.venv if one exists
if os.getenv("RIFFLE_NO_DOCTOR"):
    deps.ensure()
else:
    import doctor
    _usable, _results = doctor.run()
    if not _usable:
        sys.exit("Fix the items above (or run `python doctor.py`), then start the TUI again.")
    if any(r.status == doctor.FAIL for r in _results) and sys.stdin.isatty():
        try:
            input("Press Enter to open the TUI… ")
        except EOFError:
            pass
    deps.ensure()                 # no-op when the doctor already installed everything

import config  # noqa: E402  (loads .env, so GITHUB_TOKEN from it counts)
import dashboard  # noqa: E402
from features import llm_flags  # noqa: E402

try:
    import psutil  # noqa: E402
except ImportError:               # RAM/CPU readout is optional
    psutil = None

from rich.text import Text  # noqa: E402
from textual import on  # noqa: E402
from textual.app import App, ComposeResult  # noqa: E402
from textual.binding import Binding  # noqa: E402
from textual.containers import Horizontal, Vertical, VerticalScroll  # noqa: E402
from textual.screen import ModalScreen  # noqa: E402
from textual.widgets import (Button, Checkbox, DataTable, Footer, Header, Input,  # noqa: E402
                             Label, Log, ProgressBar, Select, Static)
from rich.highlighter import Highlighter  # noqa: E402

MODELS = [("Sonnet (default)", "sonnet"), ("Opus", "opus"), ("Haiku (fast/cheap)", "haiku"),
          ("Fable 5.1", "claude-fable-5-1")]

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(HERE, "build_dataset.py")
TEST_OUT = os.path.join(HERE, "test_output", "test_dataset.jsonl")
LOG_DIR = os.path.join(HERE, "logs")
SETTINGS = os.path.join(HERE, ".tui_settings.json")
STALL_SECS = 10 * 60              # a mining repo with no event this long is flagged
# exit codes that mean "killed by a signal", which on Linux is almost always the
# kernel OOM killer (SIGKILL) when nobody pressed Stop
KILLED = {-9: "SIGKILL", 137: "SIGKILL"}


def _exit_reason(rc: int | None) -> str | None:
    if rc in KILLED:
        return "killed (likely out of memory)"
    if rc is not None and rc < 0:
        try:
            return f"killed ({signal.Signals(-rc).name})"
        except ValueError:
            return f"killed (signal {-rc})"
    return None


class _LevelHighlighter(Highlighter):
    """Colours whole log lines by level, the way the old RichLog styles did.
    The log panel is a Log widget (not RichLog) because Log supports mouse
    selection and copy; RichLog does not."""
    def highlight(self, text) -> None:
        line = text.plain
        if ("breaker" in line or "FAILED" in line or "[error" in line or "WARNING" in line
                or "killed" in line or "out of memory" in line):
            text.stylize("bold red")
        elif line.startswith(("[warn", "[skip", "[api] rate limit", "[stop]", "[blame] warning")):
            text.stylize("yellow")
        elif line.startswith(("[web]", "[tui] saving", "[tui] log file", "[tui] saved", "[tui] peak")):
            text.stylize("cyan")
        elif line.startswith(("$ ", "[test]")):
            text.stylize("bold")


def _system_copy(text: str) -> bool:
    """Put text on the OS clipboard via the platform tool, for terminals that
    ignore OSC 52 (e.g. GNOME Terminal). Returns True on success."""
    import shutil
    import subprocess
    for cmd in (["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"],
                ["pbcopy"], ["clip"]):
        if shutil.which(cmd[0]):
            try:
                subprocess.run(cmd, input=text.encode(), timeout=5, check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
            except (OSError, subprocess.SubprocessError):
                continue
    return False


def _fmt_tokens(n: int) -> str:
    return f"{n / 1e6:.2f}M" if n >= 1e6 else f"{n / 1e3:.1f}k" if n >= 1e3 else str(n)


def _sum_usage(repos) -> dict | None:
    """This run's LLM usage: every repo miner reports its own running total."""
    us = [r.llm_usage for r in repos if r.llm_usage]
    if not us:
        return None
    tot = {k: sum(u.get(k) or 0 for u in us) for k in ("calls", "input", "output", "cache_write", "cache_read", "cost_usd")}
    tot["cost_known"] = any(u.get("cost_known") for u in us)
    tot["tokens_in"] = tot["input"] + tot["cache_write"] + tot["cache_read"]
    return tot


def _fmt_bytes(n: float) -> str:
    return f"{n / 2**30:.1f} GB" if n >= 2**30 else f"{n / 2**20:.0f} MB"


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
    szz_t0: float | None = None          # first SZZ progress event (time, done)
    szz_d0: int = 0
    szz: str = "—"                       # SZZ column: — / scanning / 45% / cached / done
    last_event: float | None = None      # for the no-progress warning
    rl_until: float | None = None        # GitHub rate-limit wait ends at
    slowest: float = 0.0                 # slowest PR (s)
    first_pr: float | None = None        # first PR finished this run (for the ETA)
    llm_usage: dict | None = None        # cumulative claude usage of this repo's miner


@dataclass
class RunStats:
    started: float | None = None
    ended: float | None = None
    pr_times: deque = field(default_factory=lambda: deque(maxlen=200))  # wall-clock stamps
    llm_secs: deque = field(default_factory=lambda: deque(maxlen=200))
    breaker: str | None = None
    batch_ok: int | None = None
    rss: int = 0                         # miner process tree, bytes
    rss_peak: int = 0
    cpu: float = 0.0                     # percent of one core, summed over the tree


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


def qr_text(data: str) -> Text | None:
    """The data as a QR code drawn with half-block characters, two modules per
    character cell. Always black on white (with a quiet zone), so a phone can
    scan it whatever the terminal theme. None if segno isn't installed."""
    try:
        import segno
    except ImportError:
        return None
    qr = segno.make(data, error="m", micro=False)
    rows = [[bool(v) for v in r] for r in qr.matrix]
    q = 2                                            # quiet zone, in modules
    w = len(rows[0]) + 2 * q
    rows = [[False] * w] * q + [[False] * q + r + [False] * q for r in rows] + [[False] * w] * q
    if len(rows) % 2:
        rows.append([False] * w)
    out = Text(no_wrap=True, overflow="crop")
    for top, bot in zip(rows[0::2], rows[1::2]):
        for t, b in zip(top, bot):
            out.append("▀", style=f"{'#000000' if t else '#ffffff'} on {'#000000' if b else '#ffffff'}")
        out.append("\n")
    return out


class QRScreen(ModalScreen):
    """Scan to open the web view on a phone; o opens it here, c copies it."""
    BINDINGS = [Binding("escape,q,ctrl+b", "app.pop_screen", "Close"),
                Binding("o", "open", "Open here"), Binding("c", "copy", "Copy link")]
    DEFAULT_CSS = """
    QRScreen { align: center middle; }
    #qrbox { width: auto; height: auto; max-width: 95%; border: round $accent; background: $surface; padding: 1 2; }
    #qrbox Static { width: auto; }
    #qr_title { text-style: bold; margin-bottom: 1; }
    #qr_hint { color: $text-muted; margin-top: 1; }
    #qr_buttons { height: 3; width: auto; margin-top: 1; }
    #qr_buttons Button { margin-right: 1; }
    """

    def __init__(self, url: str, local_url: str):
        super().__init__()
        self.url, self.local_url = url, local_url

    def compose(self) -> ComposeResult:
        code = qr_text(self.url)
        with Vertical(id="qrbox"):
            yield Static("Web view · scan with a phone on this Wi-Fi", id="qr_title")
            yield Static(code if code is not None else
                         Text("(QR needs the segno package: pip install segno)", style="yellow"))
            yield Static(Text(self.url, style="bold cyan"), id="qr_url")
            yield Static("Read-only. The key in the link changes every launch.", id="qr_hint")
            with Horizontal(id="qr_buttons"):
                yield Button("Open here (o)", id="qr_open", variant="primary")
                yield Button("Copy link (c)", id="qr_copy")
                yield Button("Close (esc)", id="qr_close")

    def action_open(self) -> None:
        webbrowser.open(self.local_url)
        self.app.notify("Opened in this computer's browser")

    def action_copy(self) -> None:
        self.app._copy(self.url)
        self.app.notify(f"Link copied: {self.url}")

    @on(Button.Pressed, "#qr_open")
    def _b_open(self) -> None:
        self.action_open()

    @on(Button.Pressed, "#qr_copy")
    def _b_copy(self) -> None:
        self.action_copy()

    @on(Button.Pressed, "#qr_close")
    def _b_close(self) -> None:
        self.app.pop_screen()


class RiffleTUI(App):
    TITLE = "Riffle miner"
    SUB_TITLE = f"build_dataset · v{__version__}"
    CSS = """
    #form { width: 42; border: round $primary; padding: 0 1; }
    #form Input { margin-bottom: 0; }
    #form Label { color: $text-muted; margin-top: 1; }
    #form Checkbox { margin-top: 1; }
    #buttons { height: 3; margin-top: 1; }
    #buttons Button { width: 1fr; }
    #main { padding: 0 1; }
    #stats { height: 9; border: round $secondary; padding: 0 1; }
    #overall { height: 1; margin: 0 0 1 0; }
    #repos { height: 1fr; min-height: 6; border: round $secondary; }
    #log { height: 1fr; min-height: 6; border: round $secondary; }
    #token_warn { color: $warning; margin-top: 1; }
    .breaker { color: $error; text-style: bold; }
    """
    BINDINGS = [
        Binding("ctrl+r", "start", "Start"),
        Binding("ctrl+x", "stop", "Stop"),
        Binding("ctrl+t", "test_llm", "Test LLM"),
        Binding("ctrl+e", "test_run", "Test run"),
        Binding("ctrl+o", "view_test", "View test data"),
        Binding("ctrl+g", "show_split", "Show split"),
        Binding("ctrl+k", "audit", "Audit data"),
        Binding("ctrl+l", "clear_log", "Clear log"),
        Binding("ctrl+y", "copy_log", "Copy log"),
        Binding("ctrl+n", "skip_repo", "Skip repo"),
        Binding("ctrl+b", "open_dashboard", "Web view / QR"),
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
        self.log_fh = None
        self.log_path: str | None = None
        self._procs: dict[int, object] = {}  # psutil.Process by pid (keeps cpu_percent state)
        self.log_tail: deque = deque(maxlen=150)   # for the web dashboard
        self.log_lines: deque = deque(maxlen=5000) # unwrapped, for ctrl+y copy
        self._final_state = "finished"
        self.plan_usage: list[dict] | None = None   # Claude plan limits from `claude -p /usage`
        self.control_path: str | None = None       # repos to skip, read by build_dataset
        self.skip_set: set[str] = set()
        self._skip_confirm: tuple[str, float] | None = None
        self._res: dict = {}                       # last RAM/CPU reading, for the dashboard
        self.dash = None if os.getenv("RIFFLE_DASHBOARD") == "0" else dashboard.Dashboard()

    # ---------------------------------------------------------------- layout
    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with VerticalScroll(id="form"):
                yield Static("", id="token_warn")
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
                yield Label("Re-mine repos (owner/name, comma-separated)")
                yield Input("", id="remine", placeholder="e.g. rust-lang/cargo", compact=True)
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
                yield Checkbox("Auto re-mine broken repos", False, id="auto_remine", compact=True)
                yield Checkbox("Skip big repos (quick test run)", False, id="skip_big", compact=True)
                yield Label("Big = more than N commits")
                yield Input("100000", id="big_n", type="integer", compact=True)
                with Horizontal(id="buttons"):
                    yield Button("Show split", id="split", variant="default")
                    yield Button("Audit", id="audit", variant="default")
                    yield Button("Test LLM", id="test", variant="primary")
                    yield Button("Test run", id="testrun", variant="warning")
                    yield Button("Start", id="start", variant="success")
                    yield Button("Stop", id="stop", variant="error", disabled=True)
            with Vertical(id="main"):
                yield Static("Idle. Configure on the left, then Start (ctrl+r).", id="stats")
                yield ProgressBar(id="overall", show_eta=False)
                yield DataTable(id="repos", zebra_stripes=True, cursor_type="row")
                yield Log(id="log", max_lines=5000, highlight=True)
        yield Footer()

    def on_mount(self) -> None:
        t = self.query_one("#repos", DataTable)
        for key, label in [("repo", "Repo"), ("stage", "Stage"), ("prs", "PRs"),
                           ("szz", "SZZ"), ("rows", "Rows"), ("skips", "Skips"),
                           ("llm", "LLM ok/fail"), ("time", "Time"), ("eta", "ETA"),
                           ("slowest", "Slowest PR")]:
            t.add_column(label, key=key)
        self.query_one("#log", Log).highlighter = _LevelHighlighter()
        self._load_settings()
        self._check_token()
        self._start_dashboard()
        self.set_interval(0.25, self._poll_events)
        self.set_interval(1.0, self._render_stats)
        if not os.getenv("RIFFLE_NO_PLAN_USAGE"):
            self._refresh_plan()
            self.set_interval(60, self._refresh_plan)

    # ----------------------------------------------------------- plan usage
    def _refresh_plan(self) -> None:
        self.run_worker(self._plan_worker, thread=True, exclusive=True, group="plan")

    def _plan_worker(self) -> None:
        res = llm_flags.claude_plan_usage()
        if res is not None:
            self.plan_usage = res

    def _plan_text(self) -> str:
        if not self.plan_usage:
            return ""
        parts = []
        for p in self.plan_usage:
            col = "red" if p["pct"] >= 90 else "yellow" if p["pct"] >= 70 else "green"
            when = ""
            if p.get("resets_at"):
                left = p["resets_at"] - time.time()
                at = time.strftime("%-I:%M%p", time.localtime(p["resets_at"])).lower()
                when = f" · resets in {_fmt_secs(left)} ({at})" if left > 0 else " · resetting now"
            elif p.get("resets"):
                when = f" · resets {p['resets'].split(' (')[0]}"
            parts.append(f"{p['label'].lower()} [{col}]{p['pct']:.0f}%[/]{when}")
        return "Claude plan: " + " · ".join(parts)

    # ------------------------------------------------------------- dashboard
    def _start_dashboard(self) -> None:
        if self.dash is None:
            return
        if not self.dash.start():
            self._log("[web] could not open a port for the web view", style="yellow")
            self.dash = None
            return
        self._log(f"[web] watch from any device on this Wi-Fi: {self.dash.url}", style="bold cyan")
        self._log("[web] ctrl+b shows a QR code to scan with your phone · read-only · "
                  "the key changes every launch", style="cyan")
        self.query_one("#stats", Static).update(
            "Idle. Configure on the left, then Start (ctrl+r).\n"
            f"[b]Web view:[/b] {self.dash.url}")
        self._publish()

    def action_open_dashboard(self) -> None:
        if self.dash is None:
            self.notify("Web view is off (RIFFLE_DASHBOARD=0 or no free port).", severity="warning")
            return
        self.push_screen(QRScreen(self.dash.url, self.dash.local_url))

    def _copy(self, text: str) -> bool:
        """Copy via the terminal (OSC 52) and the OS clipboard tool, so it works
        whichever one this terminal supports."""
        try:
            self.copy_to_clipboard(text)
        except Exception:                              # noqa: BLE001
            pass
        return _system_copy(text)

    def action_skip_repo(self) -> None:
        """Skip the repo selected in the table (or the one running). A queued
        repo is marked (press again to unmark); a running one asks for a second
        press within 3 s, since it stops that repo's mining."""
        if self.proc is None or not self.control_path:
            self.notify("Skip works during a run. For a quick run, tick 'Skip big repos'.",
                        severity="warning")
            return
        t = self.query_one("#repos", DataTable)
        name = None
        if t.row_count and t.cursor_row is not None and 0 <= t.cursor_row < t.row_count:
            name = str(t.get_row_at(t.cursor_row)[0])
        r = self.repos.get(name) if name else None
        if r is None or r.ended:
            running = [x for x in self.repos.values() if x.stage not in ("queued",) and not x.ended]
            r = running[0] if running else None
        if r is None:
            self.notify("Select a repo in the table first.", severity="warning")
            return
        if r.ended:
            self.notify(f"{r.name} is already finished.", severity="warning")
            return
        key = r.name.lower()
        if r.stage == "queued":
            if key in self.skip_set:
                self.skip_set.discard(key)
                self.notify(f"{r.name} will be mined after all")
            else:
                self.skip_set.add(key)
                self.notify(f"{r.name} will be skipped when its turn comes")
        else:
            now = time.time()
            if not (self._skip_confirm and self._skip_confirm[0] == key and now - self._skip_confirm[1] < 3):
                self._skip_confirm = (key, now)
                self.notify(f"Press ctrl+n again to stop and skip {r.name} (finished PRs are kept "
                            f"in the checkpoint; a later run resumes it).", severity="warning", timeout=3)
                return
            self._skip_confirm = None
            self.skip_set.add(key)
            self._log(f"[skip] stopping {r.name} at your request", style="yellow")
        try:
            with open(self.control_path, "w", encoding="utf-8") as fh:
                fh.write("".join(f"{x}\n" for x in sorted(self.skip_set)))
        except OSError as e:
            self.notify(f"Could not write the skip list: {e}", severity="error")
        self._update_row(r)

    def action_copy_log(self) -> None:
        text = "\n".join(self.log_lines)
        if not text:
            self.notify("The log is empty.", severity="warning")
            return
        ok = self._copy(text)
        where = "clipboard" if ok else "clipboard (via the terminal)"
        extra = f" · full log also at {self.log_path}" if self.log_path else ""
        self.notify(f"Copied {len(self.log_lines)} log lines to the {where}{extra}", timeout=6)

    @staticmethod
    def _level(style: str | None) -> str | None:
        st = style or ""
        return "bad" if "red" in st else "warn" if "yellow" in st else \
            "ok" if "green" in st else None

    def _publish(self, summary: dict | None = None) -> None:
        """Hand the web view a snapshot of what the TUI shows right now."""
        if self.dash is None:
            return
        now = time.time()
        rows = []
        try:
            t = self.query_one("#repos", DataTable)
            names = [str(t.get_row_at(i)[0]) for i in range(t.row_count)]
        except Exception:                              # noqa: BLE001
            names = list(self.repos)
        for name in names:
            r = self.repos.get(name)
            if r is None:
                continue
            v = self._row_values(r, now)
            rows.append({k: (str(x) if not isinstance(x, str) else x) for k, x in v.items()
                         if not k.endswith("_style")}
                        | {"stage_level": self._level(v["stage_style"]),
                           "szz_level": self._level(v["szz_style"]),
                           "done": r.done, "total": r.total,
                           "status": ("failed" if r.stage.startswith("failed") or r.stage == "BREAKER"
                                      else "skipped" if r.stage.startswith("skipped")
                                      else "done" if r.ended else "queued" if r.stage == "queued"
                                      else "warn" if v["stage_style"] and "yellow" in v["stage_style"]
                                      else "active")})
        snap = {"version": __version__, "state": "idle", "rows": rows, "now": now,
                "log": [{"text": txt, "level": lvl} for txt, lvl in self.log_tail],
                "out": "  +  ".join(self.out_paths), "log_path": self.log_path,
                "resources": self._res, "plan_usage": self.plan_usage,
                "llm_usage": _sum_usage(self.repos.values())}
        if summary:
            snap.update(summary)
        self.dash.publish(snap)

    # -------------------------------------------------------------- settings
    def _form_widgets(self):
        return self.query("#form Input, #form Checkbox, #form Select")

    def _load_settings(self) -> None:
        """Restore the form from the last Start (missing/old keys are ignored)."""
        try:
            with open(SETTINGS, encoding="utf-8") as fh:
                saved = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return
        for w in self._form_widgets():
            if w.id in saved:
                try:
                    w.value = saved[w.id]
                except Exception:                     # noqa: BLE001  (e.g. model removed)
                    pass

    def _save_settings(self) -> None:
        # "remine" is one-shot: remembering it would re-mine those repos on every Start
        data = {w.id: w.value for w in self._form_widgets() if w.id and w.id != "remine"}
        try:
            with open(SETTINGS, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=1, default=str)
        except OSError:
            pass

    def _check_token(self) -> bool:
        ok = bool(os.environ.get("GITHUB_TOKEN"))
        self.query_one("#token_warn", Static).update(
            "" if ok else "⚠ GITHUB_TOKEN not set (shell or .env): GitHub rate limits "
                          "will pause runs for up to an hour at a time.")
        return ok

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
                          ("skip_existing", "--skip-existing"), ("keep_clones", "--keep-clones"),
                          ("auto_remine", "--auto-remine")]:
            if self._on(wid):
                cmd.append(flag)
        if not self._on("scanners"):
            cmd.append("--no-scanners")
        if not self._on("api_contract"):
            cmd.append("--no-api-contract")
        if self._on("git_discovery"):
            cmd += ["--pr-discovery", "git"]
        for repo in [r.strip() for r in self._val("remine").split(",") if r.strip()]:
            cmd += ["--remine-repo", repo]
        if self._on("skip_big") and self._val("big_n").isdigit() and int(self._val("big_n")) > 0:
            cmd += ["--skip-big", self._val("big_n")]
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

    @on(Button.Pressed, "#audit")
    def _audit_btn(self) -> None:
        self.action_audit()

    def action_audit(self) -> None:
        """Per-repo blame coverage of the output dataset; flags repos mined
        while blame was failing and fills them into Re-mine repos."""
        self.run_worker(self._run_audit(), group="audit")

    async def _run_audit(self) -> None:
        out = self._val("out") or "dataset.parquet"
        proc = await asyncio.create_subprocess_exec(
            sys.executable, BUILD, "--out", out, "--audit", cwd=HERE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        text, _ = await proc.communicate()
        flagged = []
        for line in text.decode("utf-8", "replace").splitlines():
            bad = line.rstrip().endswith("<- re-mine")
            if bad:
                flagged.append(line.split()[0])
            self._log(line, style="bold red" if bad else
                      "yellow" if line.rstrip().endswith("<- check") else None)
        if flagged:
            box = self.query_one("#remine", Input)
            have = [r.strip() for r in box.value.split(",") if r.strip()]
            box.value = ", ".join(have + [r for r in flagged if r not in have])
            self.notify(f"{len(flagged)} repo(s) need re-mining; added to 'Re-mine repos'. "
                        "Press Start to fix them.", severity="warning", timeout=10)
        else:
            self.notify("Audit: every repo has blame data")

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
        mine = False                      # inside this PC's section (header + repos)
        for line in out.decode("utf-8", "replace").splitlines():
            if line.strip().startswith("PC "):
                mine = line.strip().startswith(f"PC {me}/")
            elif not line.startswith("    "):
                mine = False
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
        fd, self.control_path = tempfile.mkstemp(prefix="riffle-control-", suffix=".txt")
        os.close(fd)
        self.skip_set = set()
        fd, self.events_path = tempfile.mkstemp(prefix="riffle-events-", suffix=".jsonl")
        os.close(fd)
        self.events_pos = 0
        self.out_paths = self._output_paths(test)
        self._save_settings()
        self._open_log()
        self._procs.clear()
        if not self._check_token():
            self.notify("GITHUB_TOKEN is not set: expect hour-long rate-limit waits.",
                        severity="warning", timeout=8)
        cmd = self._build_cmd()
        if test:
            cmd += ["--test", "--test-prs", self._val("test_prs") or "10", "--test-repo",
                    self._val("test_repo") or "https://github.com/pallets/flask"]
        self._log(f"$ {' '.join(cmd[2:])}", style="bold")
        self._log(f"[tui] saving to: {'  +  '.join(self.out_paths)}", style="cyan")
        if self.log_path:
            self._log(f"[tui] log file: {self.log_path}", style="cyan")
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
        self.query_one("#log", Log).clear()
        self.log_lines.clear()

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
        self.query_one("#audit", Button).disabled = running
        for w in self.query("#form Input, #form Checkbox, #form Select"):
            w.disabled = running

    # ------------------------------------------------------------- subprocess
    async def _run_proc(self, cmd: list[str]) -> None:
        env = dict(os.environ, RIFFLE_EVENTS=self.events_path, PYTHONUNBUFFERED="1",
                   RIFFLE_CONTROL=self.control_path or "")
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
                if self.log_fh:   # per-PR lines go to the table, but keep them on disk
                    self.log_fh.write(time.strftime("%H:%M:%S ") + text + "\n")
                continue
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
        if rc == 130 or rc == -signal.SIGINT:
            msg = "stopped"
        elif _exit_reason(rc):
            msg = f"{_exit_reason(rc)}, exit {rc}"
        self._final_state = msg
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
        if self.stats.rss_peak:
            self._log(f"[tui] peak miner RAM: {_fmt_bytes(self.stats.rss_peak)}", style="cyan")
        if self.log_fh:
            self.log_fh.close()
            self.log_fh = None

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

    def _open_log(self) -> None:
        """One plain-text log per run under logs/, so a crash leaves evidence."""
        if self.log_fh:
            self.log_fh.close()
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            self.log_path = os.path.join(LOG_DIR, time.strftime("run-%Y%m%d-%H%M%S.log"))
            self.log_fh = open(self.log_path, "a", encoding="utf-8", buffering=1)
        except OSError:
            self.log_fh, self.log_path = None, None

    def _log(self, text: str, style: str | None = None) -> None:
        log = self.query_one("#log", Log)
        # Log doesn't soft-wrap, so wrap here to the panel width (keeps the
        # "no horizontal overflow" behaviour while staying selectable)
        width = max(40, (log.scrollable_content_region.width or log.size.width or 100) - 1)
        import textwrap
        for part in (textwrap.wrap(text, width, subsequent_indent="  ", break_on_hyphens=False,
                                   drop_whitespace=False) or [""]):
            log.write_line(part)
        self.log_lines.append(text)
        self.log_tail.append((text, self._level(style)))
        if self.log_fh:
            try:
                self.log_fh.write(time.strftime("%H:%M:%S ") + text + "\n")
            except OSError:
                pass

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
            self.query_one("#repos", DataTable).add_row(
                name, "queued", "—", "—", "0", "0", "—", "—", "—", "—", key=name)
        return self.repos[name]

    def _apply(self, e: dict) -> None:
        ev, t = e.get("ev"), e.get("t", time.time())
        resort = False
        if "repo" in e:
            r0 = self._repo(e["repo"])
            r0.last_event = t
            if ev != "rate_limit":
                r0.rl_until = None            # any other event means the wait is over
        if ev == "batch_start":
            for name in e.get("repos", []):
                self._repo(name)
        elif ev == "repo_queue":
            r = self._repo(e["repo"])
            r.stage = "cloning" if e.get("stage") == "clone" else "starting"
        elif ev == "stage":
            r = self._repo(e["repo"])
            r.stage = e.get("stage", r.stage)
        elif ev == "rate_limit":
            r = self._repo(e["repo"])
            r.rl_until = t + (e.get("wait") or 0)
        elif ev == "szz" and e.get("finished"):
            r = self._repo(e["repo"])
            r.szz = "cached" if e.get("cached") else "done"
        elif ev == "szz":
            r = self._repo(e["repo"])
            d, n = e.get("done", 0), e.get("total") or 0
            r.szz = f"{100 * d // n}%" if n else "scanning"
            if r.szz_t0 is None and n:
                r.szz_t0, r.szz_d0 = t, d
            pct = f"{100 * d // n}% " if n else ""
            eta = ""
            if r.szz_t0 is not None and d > r.szz_d0 and t > r.szz_t0:
                eta = " ETA " + _fmt_secs((n - d) * (t - r.szz_t0) / (d - r.szz_d0))
            r.stage = f"szz {pct}{d}/{n}{eta}" if n else "szz scanning"
        elif ev == "repo_start":
            r = self._repo(e["repo"])
            r.stage, r.total, r.resumed, r.started = "mining", e.get("total"), e.get("resumed", 0), t
            r.done = r.resumed
            resort = True
        elif ev == "pr":
            r = self._repo(e["repo"])
            r.done += 1
            self.stats.pr_times.append(t)
            r.slowest = max(r.slowest, e.get("secs") or 0.0)
            if e.get("llm_usage"):
                r.llm_usage = e["llm_usage"]
            if r.first_pr is None:
                r.first_pr = t
            if e.get("status") == "skip":
                r.skips += 1
            else:
                r.rows += 1
                if e.get("llm") in ("ok", "reused"):
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
            if e.get("llm_usage"):
                r.llm_usage = e["llm_usage"]
            if e.get("already"):          # mined in an earlier run: count it as done
                r.total = r.done = r.resumed = r.rows
            if e.get("status") == "skipped":
                r.total, r.done = 0, 0                 # out of this run's PR total
                r.stage = (f"skipped (big: ~{e['commits']:,} commits)"
                           if e.get("skip") == "big" and e.get("commits") else "skipped")
            else:
                r.stage = ("done (earlier run)" if e.get("already") else "done") \
                    if e.get("status") == "ok" else e.get("status", "done")
            resort = True
        elif ev == "repo_failed":
            r = self._repo(e["repo"])
            why = _exit_reason(e.get("rc"))
            r.stage, r.ended = f"failed ({e.get('stage')})" + (f" · {why}" if why else ""), t
            resort = True
            if why:
                self._log(f"[tui] {e['repo']}: {e.get('stage')} {why} (exit {e.get('rc')})",
                          style="bold red")
        elif ev == "batch_done":
            self.stats.batch_ok = e.get("ok")
        if "repo" in e:
            self._update_row(self.repos[e["repo"]])
        if resort:
            self._sort_rows()

    def _sort_rows(self) -> None:
        """Active repos first, then queued, finished/failed last (stable)."""
        order = {name: i for i, name in enumerate(self.repos)}

        def rank(name) -> tuple:
            r = self.repos.get(str(name))
            if r is None:
                return (3, 0)
            if r.ended:
                return (2, order[r.name])
            return (1 if r.stage == "queued" else 0, order[r.name])
        try:
            self.query_one("#repos", DataTable).sort("repo", key=rank)
        except Exception:                             # noqa: BLE001
            pass

    def _stage_text(self, r: RepoStat, now: float) -> Text:
        if r.rl_until and now < r.rl_until:
            return Text(f"rate-limited {_fmt_secs(r.rl_until - now)}", style="bold yellow")
        idle = now - r.last_event if r.last_event else 0
        if not r.ended and r.stage == "mining" and idle >= STALL_SECS:
            return Text(f"mining · no progress {_fmt_secs(idle)}", style="bold yellow")
        if not r.ended and r.name.lower() in self.skip_set:
            return Text("queued · will skip" if r.stage == "queued" else f"{r.stage} · stopping",
                        style="yellow")
        style = "bold red" if r.stage == "BREAKER" or r.stage.startswith("failed") \
            else "green" if r.stage.startswith("done") else "dim" if r.stage.startswith("skipped") else ""
        return Text(r.stage, style=style)

    def _row_values(self, r: RepoStat, now: float) -> dict:
        """Display values for one repo row (shared by the table and web view)."""
        stage = self._stage_text(r, now)
        prs = f"{r.done}/{r.total}" if r.total is not None else "—"
        llm = f"{r.llm_ok}/{r.llm_fail}" if (r.llm_ok or r.llm_fail) else "—"
        el = None
        if r.started:
            el = (r.ended or now) - r.started
        # per-repo ETA from this run's PR rate (resumed PRs excluded)
        eta = "—"
        mined = r.done - r.resumed
        if not r.ended and r.total and r.first_pr and mined >= 2 and now > r.first_pr:
            rate = (mined - 1) / (now - r.first_pr)
            if rate > 0:
                eta = _fmt_secs((r.total - r.done) / rate)
        szz_style = "green" if r.szz in ("cached", "done") else ""
        return {"name": r.name, "stage": stage.plain, "stage_style": str(stage.style or ""),
                "prs": prs, "szz": r.szz, "szz_style": szz_style, "rows": str(r.rows),
                "skips": str(r.skips), "llm": llm, "time": _fmt_secs(el), "eta": eta,
                "slowest": _fmt_secs(r.slowest) if r.slowest else "—"}

    def _update_row(self, r: RepoStat) -> None:
        t = self.query_one("#repos", DataTable)
        v = self._row_values(r, time.time())
        for col, val in [("stage", Text(v["stage"], style=v["stage_style"])), ("prs", v["prs"]),
                         ("szz", Text(v["szz"], style=v["szz_style"])), ("rows", v["rows"]),
                         ("skips", v["skips"]), ("llm", v["llm"]), ("time", v["time"]),
                         ("eta", v["eta"]), ("slowest", v["slowest"])]:
            try:
                t.update_cell(r.name, col, val, update_width=True)
            except Exception:                         # row not yet rendered
                pass

    # ------------------------------------------------------------------ stats
    def _render_stats(self) -> None:
        if self.stats.started is None:
            idle = "Idle. Configure on the left, then Start (ctrl+r)."
            if self.dash:
                idle += f"\n[b]Web view:[/b] {self.dash.url}"
            if self._plan_text():
                idle += "\n" + self._plan_text()
            self.query_one("#stats", Static).update(idle)
            self._publish()
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
        res = self._resources()
        if res:
            line1 += "\n" + res
        usage = _sum_usage(self.repos.values())
        utxt = ""
        if usage:
            utxt = (f"  ·  tokens in {_fmt_tokens(usage['tokens_in'])} "
                    f"(cache {_fmt_tokens(usage['cache_read'])}) / out {_fmt_tokens(usage['output'])}"
                    + (f"  ·  ${usage['cost_usd']:.2f} at API prices" if usage["cost_known"] else ""))
        line2 = (f"LLM ok {llm_ok}  fail {llm_fail}  ·  avg latency "
                 f"{f'{lat:.1f}s' if lat else '—'}{utxt}  ·  breaker "
                 + (f"[bold red]{self.stats.breaker}[/]" if self.stats.breaker else "[green]armed[/]"))
        plan = self._plan_text()
        if plan:
            line2 += "\n" + plan
        line3 = ("[b]Saving to:[/b] " if self.proc else "[b]Saved to:[/b] ") + \
            "\n            ".join(self.out_paths)
        if self.log_path:
            line3 += f"\n[b]Log:[/b] {self.log_path}"
        if self.dash:
            line3 += f"\n[b]Web view:[/b] {self.dash.url}"
        self.query_one("#stats", Static).update(line1 + "\n" + line2 + "\n" + line3)
        bar = self.query_one("#overall", ProgressBar)
        if total and not unknown:
            bar.update(total=total, progress=done)
        for r in self.repos.values():
            if r.started and not r.ended or r.rl_until:
                self._update_row(r)
        self._publish({
            "state": state if self.proc or not self.stats.ended else self._final_state,
            "elapsed": _fmt_secs(elapsed), "repos": f"{repos_done}/{len(self.repos)}",
            "prs": f"{done}/{total if total and not unknown else '?'}",
            "rate": f"{rate * 60:.1f} PR/min" if rate else None,
            "eta": _fmt_secs(eta) if self.proc and eta else None,
            "progress": (done / total) if total and not unknown else 0,
            "llm": f"{llm_ok} / {llm_fail}", "llm_latency": f"{lat:.1f}s" if lat else None,
            "llm_usage": usage,
            "breaker": self.stats.breaker,
        })

    def _resources(self) -> str | None:
        """RAM/CPU of the miner process tree (build_dataset → mine_repo → git,
        claude, SZZ workers), plus free system RAM. Amber/red as RAM runs low."""
        if psutil is None or self.proc is None:
            if psutil is None or not self.stats.rss_peak:
                return None
            return f"peak RAM {_fmt_bytes(self.stats.rss_peak)}"
        try:
            root = psutil.Process(self.proc.pid)
            tree = [root, *root.children(recursive=True)]
        except psutil.Error:
            return None
        rss = cpu = 0.0
        alive = set()
        for p in tree:
            try:
                proc = self._procs.setdefault(p.pid, p)   # reuse: cpu_percent needs history
                alive.add(p.pid)
                rss += proc.memory_info().rss
                cpu += proc.cpu_percent(None)
            except psutil.Error:
                continue
        for pid in set(self._procs) - alive:
            del self._procs[pid]
        vm = psutil.virtual_memory()
        self.stats.rss, self.stats.cpu = int(rss), cpu
        self.stats.rss_peak = max(self.stats.rss_peak, int(rss))
        free = vm.available / vm.total
        color = "bold red" if free < 0.07 else "yellow" if free < 0.15 else "green"
        self._res = {"rss": _fmt_bytes(rss), "peak": _fmt_bytes(self.stats.rss_peak),
                     "free": _fmt_bytes(vm.available), "total": _fmt_bytes(vm.total),
                     "cpu": f"{cpu:.0f}% of {psutil.cpu_count() * 100}%",
                     "level": self._level(color),
                     # numeric, for the web view's meters
                     "rss_frac": round(rss / vm.total, 4),
                     "used_frac": round(1 - free, 4),
                     "cpu_frac": round(min(1.0, cpu / (psutil.cpu_count() * 100)), 4)}
        warn = "  ← low memory" if free < 0.15 else ""
        return (f"RAM [{color}]{_fmt_bytes(rss)}[/] (peak {_fmt_bytes(self.stats.rss_peak)})  ·  "
                f"free [{color}]{_fmt_bytes(vm.available)}[/] of {_fmt_bytes(vm.total)}  ·  "
                f"CPU {cpu:.0f}% of {psutil.cpu_count() * 100}%{warn}")


if __name__ == "__main__":
    RiffleTUI().run()
