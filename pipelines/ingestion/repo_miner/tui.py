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
no progress, and saves every run's log under logs/. The form always starts from
the defaults, so every PC runs the same config unless it is changed on screen.

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
    deps.extend_path()            # user-level installs (~/go/bin, ~/.local/bin) still count
else:
    try:
        import doctor
        _usable, _results = doctor.run()
    except Exception as _e:       # noqa: BLE001 (a doctor bug must not block the TUI)
        print(f"[doctor] skipped, internal error: {type(_e).__name__}: {_e}", file=sys.stderr)
        doctor = None
        _usable, _results = True, []
    if not _usable:
        sys.exit("Fix the items above (or run `python doctor.py`), then start the TUI again.")
    if doctor and any(r.status == doctor.FAIL for r in _results) and sys.stdin.isatty():
        try:
            input("Press Enter to open the TUI… ")
        except EOFError:
            pass
    deps.ensure()                 # no-op when the doctor already installed everything
    if doctor and not any(r.status == doctor.FAIL for r in _results):
        if not doctor.countdown(5):   # time to read the report; Ctrl+C cancels
            sys.exit("Cancelled.")

import config  # noqa: E402  (loads .env, so GITHUB_TOKEN from it counts)
import dashboard  # noqa: E402
import naming  # noqa: E402
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
from textual.markup import escape as esc  # noqa: E402  (text from a join code or the store is data, never markup)
from textual.screen import ModalScreen  # noqa: E402
from textual.widgets import (Button, Checkbox, DataTable, Footer, Header, Input,  # noqa: E402
                             Label, Log, ProgressBar, Select, Static)
from rich.highlighter import Highlighter  # noqa: E402

def form_values(argv: list[str]) -> dict:
    """The form fields a command line means (the lead's, from a join code), parsed the way the miner parses it."""
    import build_dataset
    ns = build_dataset.build_parser().parse_known_args(list(argv))[0]
    vals = {"repos_file": ns.repos_file, "out": ns.out, "out_prefix": ns.out_prefix or "", "out_postfix": ns.out_postfix or "",
            "max_prs": "" if ns.full_repo or ns.max_prs is None else str(ns.max_prs), "min_age": str(ns.min_age_days),
            "window_from": ns.window_from or "", "window_to": ns.window_to or "", "cap_training": ns.cap_on == "training",
            "full_repo": bool(ns.full_repo), "only_domain": ns.only_domain or "", "llm": bool(ns.llm),
            "snapshots": bool(ns.snapshots), "szz": bool(ns.szz), "ci_history": bool(ns.ci_history),
            "merge_outcome": bool(ns.merge_outcome), "scanners": not ns.no_scanners,
            "api_contract": not ns.no_api_contract, "git_discovery": ns.pr_discovery == "git", "split_by": ns.split_by}
    if ns.lead_in_days is not None:
        vals["lead_in"] = str(ns.lead_in_days)
    if ns.llm_scope:
        vals["llm_training"] = ns.llm_scope == "training"
    if ns.close_reason_llm:
        vals["close_other_only"] = ns.close_reason_llm == "other_only"
    if ns.llm_model:
        vals["llm_model"] = ns.llm_model
    shard = naming.parse_shard(ns.shard)
    if shard:
        vals["pc_index"], vals["pcs"] = str(shard[0]), str(shard[1])
    return vals


SPLIT_BY = [("Commits (the default)", "commits"), ("PRs (balances a Full repo run)", "prs")]
MODELS = [("Haiku (default, fast/cheap)", "haiku"), ("Sonnet", "sonnet"), ("Opus", "opus"),
          ("Fable 5.1", "claude-fable-5-1")]

# the form fields a join code decides: locked after joining. Machine settings (workers, clone dir, resume
# switches, the test fields) stay editable, because PCs differ in RAM and disk.
LOCKED_IDS = frozenset((
    "repos_file", "out", "out_prefix", "out_postfix", "pcs", "pc_index", "max_prs", "min_age", "window_from",
    "window_to", "cap_training", "full_repo", "lead_in", "only_domain", "llm", "llm_model", "llm_training",
    "close_other_only", "snapshots", "szz", "ci_history", "merge_outcome", "scanners", "api_contract", "git_discovery", "split_by"))

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(HERE, "build_dataset.py")
TEST_OUT = os.path.join(HERE, "test_output", "test_dataset.jsonl")
LOG_DIR = os.path.join(HERE, "logs")
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
        elif (line.startswith(("[warn", "[skip", "[api] rate limit", "[stop]", "[blame] warning",
                               "[llm-quota]"))
              or (line.startswith("[health]") and " FLAG " in line)):
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
    llm_skip: int = 0                    # out of --llm-scope or a trivial diff
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
    llm_paused_until: float | None = None   # Claude plan limit: LLM calls wait until then
    llm_paused_why: str | None = None
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


class LeadSettingsScreen(ModalScreen):
    """What the lead chose, read-only, for a PC that joined: the repo list and who mines what, every setting that shapes the
    data, who issued it and when it expires."""

    BINDINGS = [Binding("escape,q", "app.pop_screen", "Close")]
    CSS = """
    LeadSettingsScreen { align: center middle; }
    #ls { width: 100; max-width: 98%; height: auto; max-height: 96%; border: round $primary; background: $surface; padding: 0 2 1 2; }
    #ls_title { margin-top: 1; text-style: bold; }
    #ls_info { margin-top: 1; color: $text-muted; }
    .sec { margin-top: 1; text-style: bold; }
    #ls DataTable { height: auto; max-height: 12; margin-top: 1; }
    """

    def __init__(self, info: dict, doc: dict, my_version: str):
        super().__init__()
        self.info, self.doc, self.my_version = info, doc, my_version

    def compose(self) -> ComposeResult:
        lead, d = self.info["lead"], self.doc
        exp = time.strftime("%Y-%m-%d", time.gmtime(lead["expires"])) if lead.get("expires") else "never"
        ver = lead.get("version") or d.get("v") or "?"
        same = "same as yours" if ver == self.my_version else f"yours is {self.my_version}: run git pull"
        with VerticalScroll(id="ls"):
            yield Static("The lead's settings   [dim]read-only · Esc closes[/dim]", id="ls_title")
            yield Static(f"issued by [b]{esc(str(lead.get('issued_by') or 'the lead'))}[/b] on {(lead.get('created') or '?')[:10]} · "
                         f"split [b]{esc(str(d['s']))}[/b] · you are PC [b]{d['k']}[/b] of [b]{d['n']}[/b]\n"
                         f"code valid until {exp} · miner {ver} ({same})", id="ls_info")
            yield Static("Settings every PC runs with", classes="sec")
            yield DataTable(id="ls_settings", show_header=False)
            n = len(self.info["repos"])
            yield Static(f"Repos in {esc(str(lead.get('repos_name') or 'the list'))} ({n}); yours are marked", classes="sec")
            yield DataTable(id="ls_repos")

    def on_mount(self) -> None:
        t = self.query_one("#ls_settings", DataTable)
        t.add_columns("setting", "value")
        for k, v in self.info["settings"]:
            t.add_row(k, str(v))
        r = self.query_one("#ls_repos", DataTable)
        r.add_columns("Repo", "Commits", "Mined by")
        for name, w, pc in self.info["repos"]:
            r.add_row(name, f"~{w:,}" if w else "–", f"PC {pc}" + ("  ◀ you" if pc == self.doc["k"] else ""))
        if not self.info["repos"]:
            r.add_row("(the list is not on this PC)", "", "")


class LeadScreen(ModalScreen):
    """The lead's tools, without a terminal: issue one join code per PC for the settings in the form, copy
    them, and see or revoke the PC tokens on the store. Needs the store URL and an admin token in .env."""

    BINDINGS = [Binding("escape", "app.pop_screen", "Close")]
    CSS = """
    LeadScreen { align: center middle; }
    #lead { width: 104; max-width: 98%; height: auto; max-height: 96%; border: round $primary;
            background: $surface; padding: 0 2 1 2; }
    #lead_title { height: 1; margin-top: 1; text-style: bold; }
    #lead_status { margin-top: 1; color: $text-muted; }
    #lead_msg { margin-top: 1; padding: 0 1; background: $boost; min-height: 1; }
    .sec { margin-top: 1; text-style: bold; }
    .hint { color: $text-muted; }
    .lrow { height: 1; margin-top: 1; }
    .lrow Input { width: 1fr; background: $boost; }
    .lrow .days { width: 8; }
    .lrow Button { margin-left: 1; }
    .lrow Static { width: auto; margin-right: 1; color: $text-muted; }
    #lead DataTable { height: 6; margin-top: 1; }
    """

    def __init__(self, tui_app):
        super().__init__()
        self.tui = tui_app
        self.codes: list[tuple[str, str]] = []          # (label, join code) from the last Issue
        self.split_id = ""
        self.pending: str | None = None                  # a revoke waiting for its second press

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="lead"):
            yield Static("Lead tools   [dim]Esc closes[/dim]", id="lead_title")
            yield Static("", id="lead_status")
            yield Static("Set the form on the left first, then Issue codes.", id="lead_msg")
            yield Static("Admin token", classes="sec")
            yield Static("From manage.py add-admin on the store. Checked with the store, then saved to .env.", classes="hint")
            with Horizontal(classes="lrow"):
                yield Input("", id="adm_token", password=True, placeholder="ad_…", compact=True)
                yield Button("Save", id="adm_save", compact=True)
            yield Static("Join codes", classes="sec")
            yield Static("One per PC, carrying the settings in the form. Send each only to its owner.", classes="hint")
            with Horizontal(classes="lrow"):
                yield Static("Valid for", classes="")
                yield Input("30", id="adm_days", type="integer", compact=True, classes="days")
                yield Static("days (0 = never)")
                yield Button("Issue codes", id="adm_issue", variant="primary", compact=True)
            yield DataTable(id="codes", cursor_type="row")
            with Horizontal(classes="lrow"):
                yield Button("Copy selected", id="code_copy", compact=True)
                yield Button("Copy all", id="code_all", compact=True)
                yield Button("Save to a private file", id="code_save", compact=True)
                yield Button("Join this PC as selected", id="code_join", variant="primary", compact=True)
            yield Static("PC tokens on the store", classes="sec")
            yield DataTable(id="pcs_tbl", cursor_type="row")
            with Horizontal(classes="lrow"):
                yield Button("Refresh", id="pc_refresh", compact=True)
                yield Button("Revoke selected PC", id="pc_revoke", variant="error", compact=True)
                yield Button("Revoke its whole split", id="split_revoke", variant="error", compact=True)

    def on_mount(self) -> None:
        self.query_one("#codes", DataTable).add_columns("PC", "Join code (shortened here; Copy gives the whole code)")
        self.query_one("#pcs_tbl", DataTable).add_columns("PC", "Split", "Expires", "Last upload")
        self._status()
        if self._admin_ready():
            self._refresh()

    # -- helpers ---------------------------------------------------------
    def _admin_ready(self) -> bool:
        return bool(os.environ.get("RIFFLE_SYNC_URL") and os.environ.get("RIFFLE_ADMIN_TOKEN"))

    def _status(self) -> None:
        url = os.environ.get("RIFFLE_SYNC_URL") or "(not set)"
        ok = bool(os.environ.get("RIFFLE_ADMIN_TOKEN"))
        self.query_one("#lead_status", Static).update(
            f"Store [b]{esc(url)}[/b]  ·  admin token: " + ("[green]saved[/]" if ok else "[yellow]not saved: paste it below[/]")
            + f"\nForm: [b]{self.tui._val('pcs') or '1'}[/b] PC(s) · repos file [b]{esc(str(self.tui._val('repos_file')))}[/b]")

    def _msg(self, text: str, level: str = "") -> None:
        color = {"bad": "red", "ok": "green", "warn": "yellow"}.get(level)
        text = esc(text)
        self.query_one("#lead_msg", Static).update(f"[{color}]{text}[/]" if color else text)

    def _bg(self, fn) -> None:
        self.run_worker(fn, thread=True, group="lead", exclusive=False)

    # -- buttons ---------------------------------------------------------
    @on(Button.Pressed, "#adm_save")
    def _save_token(self) -> None:
        token = self.query_one("#adm_token", Input).value.strip()
        if not token.startswith("ad_"):
            self._msg("An admin token starts with ad_. (Read tokens start with rd_ and cannot issue codes.)", "bad")
            return
        self._msg("Checking the token with the store…")

        def work() -> None:
            import admin
            import join
            saved = {k: os.environ.get(k) for k in ("RIFFLE_ADMIN_TOKEN",)}
            os.environ["RIFFLE_ADMIN_TOKEN"] = token
            try:
                me = admin.call("GET", "/v1/whoami")
                if me.get("kind") != "admins":
                    raise admin.AdminError(f"that is a {str(me.get('kind')).rstrip('s')} token, not an admin token")
                join.write_env(os.path.join(HERE, ".env"), {"RIFFLE_ADMIN_TOKEN": token})
            except admin.AdminError as e:
                if saved["RIFFLE_ADMIN_TOKEN"] is None:
                    os.environ.pop("RIFFLE_ADMIN_TOKEN", None)
                else:
                    os.environ["RIFFLE_ADMIN_TOKEN"] = saved["RIFFLE_ADMIN_TOKEN"]
                self.app.call_from_thread(self._msg, f"Not saved: {e}", "bad")
                return
            self.app.call_from_thread(self._token_saved)
        self._bg(work)

    def _token_saved(self) -> None:
        self.query_one("#adm_token", Input).value = ""
        self._status()
        self._msg("Admin token saved to .env.", "ok")
        self._refresh(keep_msg=True)

    @on(Button.Pressed, "#adm_issue")
    def _issue(self) -> None:
        if not self._admin_ready():
            self._msg("Save the store URL (RIFFLE_SYNC_URL in .env) and an admin token first.", "bad")
            return
        pcs = self.tui._val("pcs")
        if not pcs.isdigit() or int(pcs) < 2:
            self._msg("Set \"Number of PCs\" in the form to 2 or more, then issue codes.", "bad")
            return
        if not self.tui._shard_ok() or not self.tui._window_ok():
            return
        days = self.query_one("#adm_days", Input).value.strip() or "0"
        n = int(pcs)
        try:
            import join
            flags = join.shared_argv(self.tui._build_cmd()[3:])
        except ValueError as e:
            self._msg(str(e), "bad")
            return
        self._msg(f"Planning the split over {n} PCs and asking the store for {n} tokens…")

        def work() -> None:
            import subprocess
            fd, path = tempfile.mkstemp(prefix="riffle-codes-")          # mkstemp files are private (600)
            os.close(fd)
            try:
                cmd = [sys.executable, BUILD, *flags, "--shard-plan", str(n), "--issue-tokens",
                       "--token-days", days, "--tokens-out", path]
                r = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True, timeout=180, env=dict(os.environ))
                with open(path, encoding="utf-8") as fh:
                    rows = [ln.rstrip("\n").split("\t", 1) for ln in fh if "\t" in ln]
            except (subprocess.TimeoutExpired, OSError) as e:
                self.app.call_from_thread(self._msg, f"Could not issue codes: {e}", "bad")
                return
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass
            sid = next((ln.split("split ID ")[1].split(":")[0] for ln in r.stdout.splitlines() if "split ID " in ln), "")
            if r.returncode != 0 or not rows:
                tail = (r.stderr.strip().splitlines() or r.stdout.strip().splitlines() or ["no output"])[-1]
                self.app.call_from_thread(self._msg, f"Could not issue codes: {tail}", "bad")
                return
            self.app.call_from_thread(self._show_codes, sid, [(a, b) for a, b in rows])
        self._bg(work)

    def _show_codes(self, sid: str, rows: list[tuple[str, str]]) -> None:
        self.codes, self.split_id = rows, sid
        t = self.query_one("#codes", DataTable)
        t.clear()
        for label, code in rows:
            t.add_row(label, code[:22] + "…" + code[-8:], key=label)
        self._msg(f"Split {sid}: {len(rows)} codes. Copy each one and send it only to that PC's owner. "
                  f"Issuing again for this split replaces these codes.", "ok")
        self._refresh(keep_msg=True)

    def _selected_code(self) -> tuple[str, str] | None:
        t = self.query_one("#codes", DataTable)
        if not self.codes or t.cursor_row is None or t.cursor_row >= len(self.codes):
            return None
        return self.codes[t.cursor_row]

    @on(Button.Pressed, "#code_copy")
    def _copy_one(self) -> None:
        sel = self._selected_code()
        if not sel:
            self._msg("Issue codes first, then pick a row.", "warn")
            return
        ok = self.tui._copy(sel[1])
        self._msg(f"Copied the code for {sel[0]}." if ok else "No clipboard here; use Save to a private file.", "ok" if ok else "warn")

    @on(Button.Pressed, "#code_join")
    def _join_here(self) -> None:
        """The lead's own PC is one of the PCs: join with the selected code without pasting it (same path as the Join box)."""
        sel = self._selected_code()
        if not sel:
            self._msg("Issue codes first, then pick the row for this PC (PC 1 is the first).", "warn")
            return
        self.tui.query_one("#join_code", Input).value = sel[1]
        self.dismiss()
        self.tui.action_join()

    @on(Button.Pressed, "#code_all")
    def _copy_all(self) -> None:
        if not self.codes:
            self._msg("Issue codes first.", "warn")
            return
        ok = self.tui._copy("\n".join(f"{a}\t{b}" for a, b in self.codes))
        self._msg("Copied all codes, one per line." if ok else "No clipboard here; use Save to a private file.", "ok" if ok else "warn")

    @on(Button.Pressed, "#code_save")
    def _save_codes(self) -> None:
        if not self.codes:
            self._msg("Issue codes first.", "warn")
            return
        path = os.path.join(HERE, f"join_codes_{self.split_id or 'split'}.txt")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("\n".join(f"{a}\t{b}" for a, b in self.codes) + "\n")
        self._msg(f"Saved to {path} (readable only by you). Delete it once the codes are sent.", "ok")

    # -- PC tokens ---------------------------------------------------------
    @on(Button.Pressed, "#pc_refresh")
    def _refresh_btn(self) -> None:
        self._refresh()

    def _refresh(self, keep_msg: bool = False) -> None:
        """Reload the PC table. keep_msg leaves the message line alone (the caller just wrote one)."""
        if not self._admin_ready():
            return

        def work() -> None:
            import admin
            try:
                rows = admin.call("GET", "/v1/admin/pcs")["pcs"]
            except admin.AdminError as e:
                self.app.call_from_thread(self._msg, f"Could not list PCs: {e}", "bad")
                return
            self.app.call_from_thread(self._show_pcs, rows, keep_msg)
        self._bg(work)

    def _show_pcs(self, rows: list[dict], keep_msg: bool = False) -> None:
        import admin
        t = self.query_one("#pcs_tbl", DataTable)
        t.clear()
        for r in rows:
            exp = time.strftime("%Y-%m-%d", time.gmtime(r["expires"])) if r.get("expires") else "never"
            t.add_row(r["pc"], r.get("split") or "-", exp, admin.ago(r.get("last_upload")), key=r["pc"])
        self._pcs = rows
        if not rows and not keep_msg:
            self._msg("No PC tokens issued yet.")

    def _selected_pc(self) -> dict | None:
        t = self.query_one("#pcs_tbl", DataTable)
        rows = getattr(self, "_pcs", [])
        if not rows or t.cursor_row is None or t.cursor_row >= len(rows):
            return None
        return rows[t.cursor_row]

    def _revoke(self, what: str, body: dict, label: str) -> None:
        if self.pending != what:                              # two presses: the first only asks
            self.pending = what
            self._msg(f"Press the same button again to revoke {label}. It stops that PC at once.", "warn")
            return
        self.pending = None

        def work() -> None:
            import admin
            try:
                gone = admin.call("POST", "/v1/admin/revoke", body)["revoked"]
            except admin.AdminError as e:
                self.app.call_from_thread(self._msg, f"Not revoked: {e}", "bad")
                return
            self.app.call_from_thread(self._msg, "Revoked: " + (", ".join(gone) or "nothing"), "ok")
            self.app.call_from_thread(self._refresh)
        self._bg(work)

    @on(Button.Pressed, "#pc_revoke")
    def _revoke_pc(self) -> None:
        pc = self._selected_pc()
        if not pc:
            self._msg("Pick a PC in the table first.", "warn")
            return
        self._revoke("pc:" + pc["pc"], {"name": pc["pc"], "kind": "pcs"}, pc["pc"])

    @on(Button.Pressed, "#split_revoke")
    def _revoke_split(self) -> None:
        pc = self._selected_pc()
        if not pc or not pc.get("split"):
            self._msg("Pick a PC of that split in the table first.", "warn")
            return
        self._revoke("split:" + pc["split"], {"split_id": pc["split"]}, f"every PC of split {pc['split']}")


class RiffleTUI(App):
    TITLE = "Riffle miner"
    SUB_TITLE = f"build_dataset · v{__version__}"
    CSS = """
    #side { width: 44; }
    #form { height: 1fr; border: round $primary; padding: 0 1; }
    #actions { height: auto; border: round $primary; padding: 0 1; }
    #mainbtns { height: 3; }
    #mainbtns Button { width: 1fr; }
    #morebtns { height: 1; margin-top: 1; }
    #morebtns Button { width: 1fr; min-width: 6; }
    #join_msg { height: auto; color: $text-muted; }
    #lead_card { height: auto; margin-top: 1; padding: 0 1; border-left: thick $success; display: none; }
    #storerow { height: 1; margin-top: 1; }
    #storerow Button { width: 1fr; min-width: 7; }
    #leave { display: none; }
    #form Input:disabled, #form Select:disabled { text-style: italic; opacity: 70%; }
    #form Checkbox:disabled { opacity: 70%; }
    #form Input { margin-bottom: 0; }
    #form Label { color: $text-muted; margin-top: 1; }
    #form Checkbox { margin-top: 1; }
    #main { padding: 0 1; }
    #stats { height: 9; border: round $secondary; padding: 0 1; }
    #overall { height: 1; margin: 0 0 1 0; }
    #repos { height: 1fr; min-height: 6; border: round $secondary; }
    #log { height: 1fr; min-height: 6; border: round $secondary; }
    #token_warn { color: $warning; margin-top: 1; }
    #store_line { margin-top: 1; }
    .breaker { color: $error; text-style: bold; }
    """
    BINDINGS = [
        Binding("ctrl+r", "start", "Start"),
        Binding("ctrl+x", "stop", "Stop"),
        Binding("f2", "join", "Join"),
        Binding("f3", "lead", "Lead"),
        Binding("f4", "open_store", "Store"),
        Binding("f5", "lead_settings", "Settings"),
        Binding("ctrl+g", "show_split", "Split"),
        Binding("ctrl+b", "open_dashboard", "Web view"),
        Binding("ctrl+q", "quit", "Quit"),
        # still work, but the footer keeps to the keys people use (each has a button or is in the docs)
        Binding("ctrl+t", "test_llm", "Test LLM", show=False),
        Binding("ctrl+e", "test_run", "Test run", show=False),
        Binding("ctrl+o", "view_test", "View test data", show=False),
        Binding("ctrl+k", "audit", "Audit data", show=False),
        Binding("ctrl+l", "clear_log", "Clear log", show=False),
        Binding("ctrl+y", "copy_log", "Copy log", show=False),
        Binding("ctrl+n", "skip_repo", "Skip repo", show=False),
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
        self.assembly: dict | None = None      # snapshot-table build: running | done | failed
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
        self.store_state: dict | None = None       # the last answer of the team store's whoami
        self.joined: dict | None = None            # the join code's contents, once joined in this session
        self.lead_argv: list[str] | None = None    # the lead's flags + this PC's --shard, to keep the form in line
        self.sync_info: dict | None = None         # the miner's last "sync" event
        self._quitting = False
        self._hint_shown = False

    # ---------------------------------------------------------------- layout
    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with Vertical(id="side"):
                with VerticalScroll(id="form"):
                    yield Static("", id="token_warn")
                    yield Static("", id="store_line")
                    yield Static("", id="lead_card")
                    yield Label("Join code from the lead (riffle1.…)")
                    yield Input("", id="join_code", placeholder="paste it here, then Join (F2)", password=True,
                                compact=True)
                    yield Static("", id="join_msg")
                    with Horizontal(id="storerow"):
                        yield Button("Join F2", id="join", variant="primary", compact=True)
                        yield Button("Lead F3", id="lead_btn", compact=True)
                        yield Button("Store F4", id="store_btn", compact=True)
                        yield Button("Leave", id="leave", variant="warning", compact=True)
                    yield Label("Repos file")
                    yield Input("repos.txt", id="repos_file", compact=True)
                    yield Label("Output dataset")
                    yield Input("dataset.parquet", id="out", compact=True)
                    yield Label("Name prefix / postfix (blank = auto pc tag; {pc} {n} {host} {date})")
                    yield Input("", id="out_prefix", placeholder="prefix", compact=True)
                    yield Input("", id="out_postfix", placeholder="postfix", compact=True)
                    yield Label("Number of PCs (1 = mine everything here)")
                    yield Input("1", id="pcs", type="integer", compact=True)
                    yield Label("This PC # (1..PCs)")
                    yield Input("1", id="pc_index", type="integer", compact=True)
                    yield Label("Split repos between PCs by")
                    yield Select(SPLIT_BY, value="commits", allow_blank=False, id="split_by", compact=True)
                    yield Label("Clone dir")
                    yield Input("cloned_repos", id="clone_dir", compact=True)
                    yield Checkbox("Full repo: every closed PR, no cap or window", False, id="full_repo",
                                   compact=True)
                    yield Label("Max PRs per repo (blank = all)")
                    yield Input("100", id="max_prs", type="integer", compact=True)
                    yield Label("Skip PRs merged in last N days (mature labels)")
                    yield Input("90", id="min_age", type="integer", compact=True)
                    yield Label("Date window from (YYYY-MM-DD; blank = newest-N mode)")
                    yield Input("", id="window_from", placeholder="2023-07-01", compact=True)
                    yield Label("Date window to (YYYY-MM-DD)")
                    yield Input("", id="window_to", placeholder="2026-07-31", compact=True)
                    yield Checkbox("Max PRs counts training PRs only (window)", False, id="cap_training",
                                   compact=True)
                    yield Label("History lead-in days (window; 0 = off)")
                    yield Input("180", id="lead_in", type="integer", compact=True)
                    yield Label("Only repos tagged [domain: NAME] (blank = all)")
                    yield Input("", id="only_domain", placeholder="web-ts", compact=True)
                    yield Label("PR workers")
                    yield Input(str(config.default_workers()), id="workers", type="integer", compact=True)
                    yield Label("LLM concurrency")
                    yield Input("4", id="llm_conc", type="integer", compact=True)
                    yield Label("Re-mine repos (owner/name, comma-separated)")
                    yield Input("", id="remine", placeholder="e.g. rust-lang/cargo", compact=True)
                    yield Label("Test repo (Test run only)")
                    yield Input("https://github.com/pallets/flask", id="test_repo", compact=True)
                    yield Label("Test PRs")
                    yield Input("10", id="test_prs", type="integer", compact=True)
                    yield Label("LLM model")
                    yield Select(MODELS, value="haiku", allow_blank=False, id="llm_model", compact=True)
                    yield Checkbox("LLM flags (claude -p)", True, id="llm", compact=True)
                    yield Checkbox("LLM on training PRs only", True, id="llm_training", compact=True)
                    yield Checkbox("Close reason: LLM only when rules are unsure", True,
                                   id="close_other_only", compact=True)
                    yield Checkbox("Wait for Claude plan reset (don't fail calls)", True,
                                   id="quota_wait", compact=True)
                    yield Checkbox("PR snapshots (capture while mining, tables built at the end)", True,
                                   id="snapshots",
                                   compact=True)
                    yield Checkbox("SZZ label", True, id="szz", compact=True)
                    yield Checkbox("CI history", True, id="ci_history", compact=True)
                    yield Checkbox("Merge outcome (+ unmerged PRs)", True, id="merge_outcome",
                                   compact=True)
                    yield Checkbox("Scanners (lizard/semgrep)", True, id="scanners", compact=True)
                    yield Checkbox("API contract diff", True, id="api_contract", compact=True)
                    yield Checkbox("Git PR discovery", False, id="git_discovery", compact=True)
                    yield Checkbox("Reuse clones (resume)", True, id="skip_existing", compact=True)
                    yield Checkbox("Keep clones", False, id="keep_clones", compact=True)
                    yield Checkbox("Auto re-mine broken repos", False, id="auto_remine", compact=True)
                    yield Checkbox("Skip big repos (quick test run)", False, id="skip_big", compact=True)
                    yield Label("Big = more than N commits")
                    yield Input("100000", id="big_n", type="integer", compact=True)
                with Vertical(id="actions"):
                    with Horizontal(id="mainbtns"):
                        yield Button("Start  ^r", id="start", variant="success")
                        yield Button("Stop  ^x", id="stop", variant="error", disabled=True)
                    with Horizontal(id="morebtns"):
                        yield Button("Split", id="split", compact=True)
                        yield Button("Audit", id="audit", compact=True)
                        yield Button("Test LLM", id="test", compact=True)
                        yield Button("Test run", id="testrun", compact=True)
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
        self._check_token()
        self._check_store()
        self.set_interval(60, self._check_store)                # a revoke, rotation or expiry shows within a minute
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
                at = time.strftime("%I:%M%p", time.localtime(p["resets_at"])).lstrip("0").lower()
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

    # -------------------------------------------------------------- checks
    def _check_token(self) -> bool:
        ok = bool(os.environ.get("GITHUB_TOKEN"))
        self.query_one("#token_warn", Static).update(
            "" if ok else "⚠ GITHUB_TOKEN not set (shell or .env): GitHub rate limits "
                          "will pause runs for up to an hour at a time.")
        return ok

    # ------------------------------------------------------------- team store
    def _check_store(self) -> None:
        """Ask the store who this PC's token is, off the UI thread, and show the answer."""
        def work() -> None:
            import sync
            r = sync.whoami()
            self.call_from_thread(self._show_store, r)
        self.run_worker(work, thread=True, group="store", exclusive=True)

    def _show_store(self, r: dict) -> None:
        self.store_state = r
        if r.get("unset"):
            text = "[b]Team store[/b]\n[yellow]not joined[/] · paste the lead's code below"
        elif r["ok"]:
            text = (f"[b]Team store[/b]\n[green]✓ joined as {esc(str(r['name']))}[/]\n"
                    f"[dim]split {esc(str(r.get('split') or 'any'))} · uploads each repo and on Stop[/]")
        elif r.get("unreachable"):
            text = f"[b]Team store[/b]\n[yellow]unreachable[/] · {esc(str(r['why']))}\n[dim]everything saves here, uploads when it can[/]"
        else:
            text = f"[b]Team store[/b]\n[red]✗ {esc(str(r['why']))}[/]\n[dim]ask the lead for a new code and paste it below[/]"
        self.query_one("#store_line", Static).update(text)
        if (r.get("ok") or r.get("unreachable")) and self.lead_argv is None:     # a store that is down is not a rejection
            self._restore_join(r)
        if r.get("ok") and self.lead_argv is None:
            self._join_msg("This PC has a store token but no saved join code, so the lead's settings are not applied. "
                           "Paste the code again and press Join (F2), or press Leave.", "warn")
        self._update_leave()

    @on(Button.Pressed, "#join")
    def _join_btn(self) -> None:
        self.action_join()

    @on(Button.Pressed, "#store_btn")
    def _store_btn(self) -> None:
        self.action_open_store()

    def action_open_store(self) -> None:
        """Open the store's pages in the browser; if a read token is saved (python doctor.py), copy it so you can paste it
        on the sign-in page."""
        url = (os.environ.get("RIFFLE_SYNC_URL") or "").rstrip("/")
        if not url:
            self.notify("Join first (paste the join code), then the store's address is known.", severity="warning")
            return
        token = os.environ.get("RIFFLE_READ_TOKEN")
        copied = bool(token) and self._copy(token)
        try:
            webbrowser.open(url)
        except Exception:                                   # noqa: BLE001
            pass
        self.notify(f"Opened {url}. " + ("The read token is copied: paste it to sign in." if copied else
                    "No read token saved: run python doctor.py to save one, or ask the lead for one."), timeout=10)

    @on(Button.Pressed, "#lead_btn")
    def _lead_btn(self) -> None:
        self.action_lead()

    def action_lead(self) -> None:
        """Issue join codes and manage PC tokens (the lead's side)."""
        self.push_screen(LeadScreen(self))

    def action_join(self) -> None:
        """Join with the pasted code (checks it with the store, saves .env, fills the form from the
        lead's flags); with the box empty, put the lead's settings back."""
        import join
        code = self._val("join_code")
        if not code:
            if self.joined and self.lead_argv:
                self._apply_form(form_values(self.lead_argv))
                self._join_msg("The form is back to the lead's settings.", "ok")
            else:
                self._join_msg("Paste the join code the lead sent you first.", "warn")
            return
        try:
            doc = join.decode(code)
        except ValueError as e:
            self._join_msg(str(e), "bad")
            return
        self._join_msg("Checking the code with the store…")

        def work() -> None:
            import sync
            r = sync.whoami(doc["u"], doc["t"])
            d2 = doc
            if r["ok"]:
                join.write_env(os.path.join(HERE, ".env"), {"RIFFLE_SYNC_URL": doc["u"], "RIFFLE_SYNC_TOKEN": doc["t"]})
                os.environ["RIFFLE_SYNC_URL"], os.environ["RIFFLE_SYNC_TOKEN"] = doc["u"], doc["t"]
                d2 = join.apply_split(doc, sync.fetch_split(doc["u"], doc["t"]), HERE)    # the lead's repo list, not our own file
            self.call_from_thread(self._joined, d2, r)
        self.run_worker(work, thread=True, group="join", exclusive=True)

    def _join_msg(self, text: str, level: str = "") -> None:
        """A message under the join box that stays until the next action (a toast disappears too soon to read)."""
        color = {"bad": "red", "ok": "green", "warn": "yellow"}.get(level)
        self._hint_shown = False
        text = esc(text)
        self.query_one("#join_msg", Static).update(f"[{color}]{text}[/]" if color else text)

    @on(Input.Changed, "#join_code")
    def _join_typed(self, event: Input.Changed) -> None:
        n = len(event.value.strip())
        if n:
            self._join_msg(f"{n} characters pasted. Press Join (F2).")
            self._hint_shown = True
        elif self._hint_shown:                       # emptied by hand: drop our hint, but never wipe a result
            self._join_msg("")

    def _joined(self, doc: dict, r: dict) -> None:
        import join
        if not r["ok"]:
            self._join_msg(f"Not saved: {r['why']}. Ask the lead for a new code.", "bad")
            return
        self.joined = doc
        self.lead_argv = list(doc.get("a", [])) + ["--shard", f"{doc['k']}/{doc['n']}"]
        join.save_state(os.path.join(HERE, join.STATE_FILE), doc)
        self.query_one("#join_code", Input).value = ""          # the code is a secret: do not leave it on screen
        self._show_store(r)
        self._lock_form(True)
        self._join_msg("Joined. The lead's settings are filled in and locked. Leave the split to change them.", "ok")
        try:
            self._apply_form(form_values(self.lead_argv))
        except ValueError as e:
            self.notify(str(e), severity="error")
        self._log(f"[tui] joined as {r['name']}: PC {doc['k']} of {doc['n']}, split {doc['s']}. The form now has the "
                  f"lead's flags; Start uploads to {doc['u']}", style="bold green")
        if doc.get("v") and doc["v"] != __version__:
            self._log(f"[tui] WARNING: the lead used miner {doc['v']} and you have {__version__}. Run git pull and "
                      f"restart, or the merge will refuse your files.", style="bold yellow")
            self.notify(f"Miner version differs from the lead's ({doc['v']}). git pull first.", severity="warning", timeout=10)
        else:
            self.notify(f"Joined as {r['name']}. Form filled from the lead's flags.")

    def _lock_form(self, on: bool) -> None:
        """Lock (or unlock) the fields the lead's join code decides. Called when joining, leaving and restoring."""
        for wid in LOCKED_IDS:
            try:
                w = self.query_one(f"#{wid}")
                w.disabled = on
                w.tooltip = "Set by the lead. Leave the split to change it." if on else None
            except Exception:                                   # noqa: BLE001
                continue
        self._update_leave()
        self._show_card()

    def _update_leave(self) -> None:
        """Leave shows whenever this PC is tied to the store: locked to the lead's settings, or just holding a token."""
        try:
            self.query_one("#leave", Button).display = bool(self.lead_argv is not None or os.environ.get("RIFFLE_SYNC_TOKEN"))
        except Exception:                                       # noqa: BLE001
            pass

    def _restore_join(self, r: dict) -> None:
        """A TUI restarted after joining: the lead's settings come back from .join.json and stay locked, as long as the
        store still accepts the token for that split."""
        import join
        doc = join.load_state(os.path.join(HERE, join.STATE_FILE))
        if not doc or not (r.get("ok") or r.get("unreachable")) or (r.get("split") and r["split"] != doc.get("s")):
            return
        self.joined = doc
        self.lead_argv = list(doc.get("a", [])) + ["--shard", f"{doc['k']}/{doc['n']}"]
        try:
            self._apply_form(form_values(self.lead_argv))
        except ValueError:
            return
        self._lock_form(True)
        self._join_msg("The lead's settings are locked in. Leave the split to change them.", "ok")

    def _describe(self) -> dict | None:
        import join
        if not self.joined:
            return None
        try:
            return join.describe(self.joined, HERE)
        except Exception:                                   # noqa: BLE001  (a card must never break the form)
            return None

    def _show_card(self) -> None:
        """A short, always-visible summary of what the lead chose, under the store status."""
        card = self.query_one("#lead_card", Static)
        if not self.joined:
            card.update("")
            card.display = False
            return
        d, info = self.joined, self._describe()
        lead = d.get("lead") or {}
        lines = ["[b]Lead's settings[/b] · locked"]
        name = lead.get("repos_name") or "repos list"
        lines.append(f"file [b]{esc(str(name))}[/b]" + (f" · {len(info['repos'])} repos" if info and info["repos"] else ""))
        if info and info["mine"]:
            lines.append("yours: " + esc(", ".join(info["mine"])))
        if info:
            s = dict(info["settings"])
            lines.append(f"{s['Max PRs per repo']} PRs/repo · LLM {s['LLM flags']} · SZZ {s['SZZ label']}")
        lines.append(f"[dim]split {esc(str(d['s']))} · PC {d['k']} of {d['n']} · F5 for all[/]")
        card.update("\n".join(lines))
        card.display = True

    def action_lead_settings(self) -> None:
        info = self._describe()
        if not info:
            self.notify("Join first: these are the settings the lead chose.", severity="warning")
            return
        self.push_screen(LeadSettingsScreen(info, self.joined, __version__))

    @on(Button.Pressed, "#leave")
    def _leave_btn(self) -> None:
        self.action_leave()

    def action_leave(self) -> None:
        """Unlock the form: forget the lead's settings on this PC. Uploads stop while your settings make a different split."""
        import join
        if self.lead_argv is None:                              # only a token on this PC (no saved join code): forget the token
            if not os.environ.get("RIFFLE_SYNC_TOKEN"):
                return
            join.write_env(os.path.join(HERE, ".env"), {"RIFFLE_SYNC_TOKEN": ""})      # the URL stays: Lead tools need it
            os.environ.pop("RIFFLE_SYNC_TOKEN", None)
            self._update_leave()
            self._check_store()
            self._join_msg("Left the team: this PC no longer uploads. The store address is kept; join again with a code from the lead.", "warn")
            return
        self.joined = self.lead_argv = None
        join.clear_state(os.path.join(HERE, join.STATE_FILE))
        self._lock_form(False)
        self._join_msg("Unlocked. Your settings are your own now; uploads turn off while they make a different split.", "warn")

    def _apply_form(self, vals: dict) -> None:
        for wid, v in vals.items():
            try:
                w = self.query_one(f"#{wid}")
            except Exception:                                   # noqa: BLE001
                continue
            if isinstance(w, Checkbox):
                w.value = bool(v)
            elif isinstance(w, Input):
                w.value = str(v)
            elif isinstance(w, Select) and v in [m[1] for m in MODELS + SPLIT_BY]:
                w.value = v

    def _lead_differences(self) -> list[tuple[str, object, object]]:
        """Settings that shape the dataset which differ from the lead's flags (empty when not joined)."""
        if not self.lead_argv:
            return []
        import join
        return join.differences(self.lead_argv, self._build_cmd()[3:])

    # ---------------------------------------------------------------- actions
    def _val(self, wid: str) -> str:
        return self.query_one(f"#{wid}", Input).value.strip()

    def _on(self, wid: str) -> bool:
        return self.query_one(f"#{wid}", Checkbox).value

    def _select(self, wid: str) -> str:
        v = self.query_one(f"#{wid}", Select).value
        return v if isinstance(v, str) else ""

    def _build_cmd(self) -> list[str]:
        cmd = [sys.executable, "-u", BUILD,
               "--repos-file", self._val("repos_file") or "repos.txt",
               "--out", self._val("out") or "dataset.parquet",
               "--clone-dir", self._val("clone_dir") or "cloned_repos"]
        full = self._on("full_repo")
        if full:
            cmd.append("--full-repo")
        elif self._val("max_prs"):
            cmd += ["--max-prs", self._val("max_prs")]
        if self._val("min_age"):
            cmd += ["--min-age-days", self._val("min_age")]
        if not full and self._val("window_from") and self._val("window_to"):
            cmd += ["--window-from", self._val("window_from"), "--window-to", self._val("window_to")]
            if self._on("cap_training"):
                cmd += ["--cap-on", "training"]
            if self._val("lead_in"):
                cmd += ["--lead-in-days", self._val("lead_in")]
        if self._val("only_domain"):
            cmd += ["--only-domain", self._val("only_domain")]
        cmd += ["--llm-scope", "training" if self._on("llm_training") else "all",
                "--close-reason-llm", "other_only" if self._on("close_other_only") else "always"]
        cmd.append("--llm-quota-wait" if self._on("quota_wait") else "--no-llm-quota-wait")
        cmd.append("--snapshots" if self._on("snapshots") else "--no-snapshots")
        shard = self._shard()
        if shard:
            cmd += ["--shard", shard]
            if self._select("split_by") == "prs":                    # only a non-default value travels
                cmd += ["--split-by", "prs"]
        if self._val("out_prefix"):
            cmd += ["--out-prefix", self._val("out_prefix")]
        if self._val("out_postfix"):
            cmd += ["--out-postfix", self._val("out_postfix")]
        if self._val("workers"):
            cmd += ["--workers", self._val("workers")]
        if self._val("llm_conc"):
            cmd += ["--llm-concurrency", self._val("llm_conc")]
        cmd += ["--llm-model", self._model()]
        for wid, flag in [("llm", "llm"), ("szz", "szz"), ("ci_history", "ci-history")]:
            cmd.append(f"--{flag}" if self._on(wid) else f"--no-{flag}")
        for wid, flag in [("skip_existing", "--skip-existing"), ("keep_clones", "--keep-clones"),
                          ("auto_remine", "--auto-remine")]:
            if self._on(wid):
                cmd.append(flag)
        if not self._on("merge_outcome"):
            cmd.append("--no-merge-outcome")
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
        out = naming.tagged_out(self._val("out") or "dataset.parquet", self._shard(),
                                self._val("out_prefix"), self._val("out_postfix"))
        out = out if os.path.isabs(out) else os.path.join(HERE, out)
        if out.endswith(".parquet"):
            import importlib.util
            if not (importlib.util.find_spec("pandas") and importlib.util.find_spec("pyarrow")):
                out = out.rsplit(".", 1)[0] + ".jsonl"      # miner's fallback
        return [out, *naming.snapshot_outs(out)] if self._on("snapshots") else [out]

    def _shard(self) -> str | None:
        """"K/N" for this PC, or None when mining everything on one machine."""
        pcs, k = self._val("pcs"), self._val("pc_index")
        n = int(pcs) if pcs.isdigit() else 1
        i = int(k) if k.isdigit() else 1
        return f"{i}/{n}" if n > 1 else None

    def _window_ok(self) -> bool:
        """Both window dates or neither, as YYYY-MM-DD, from before to; the
        training cap and the lead-in need a window."""
        import re as _re
        a, b = self._val("window_from"), self._val("window_to")
        if bool(a) != bool(b):
            self.notify("Set both window dates, or leave both blank.", severity="error")
            return False
        if a and not (_re.fullmatch(r"\d{4}-\d{2}-\d{2}", a) and _re.fullmatch(r"\d{4}-\d{2}-\d{2}", b)
                      and a <= b):
            self.notify("Window dates must be YYYY-MM-DD, from on or before to.", severity="error")
            return False
        if not a and self._on("cap_training"):
            self.notify("'Max PRs counts training PRs only' needs a date window.", severity="error")
            return False
        return True

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
        shard = ["--shard", self._shard()] if self._shard() else []
        for flag, wid in (("--out-prefix", "out_prefix"), ("--out-postfix", "out_postfix")):
            if self._val(wid):
                shard += [flag, self._val(wid)]
        proc = await asyncio.create_subprocess_exec(
            sys.executable, BUILD, "--out", out, *shard, "--audit", cwd=HERE,
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
            "--shard-plan", self._val("pcs") or "1", "--split-by", self._select("split_by") or "commits", cwd=HERE,
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
        self._check_store()                                     # refresh the card; the miner re-checks the token itself
        if self.proc is not None:
            self.notify("A run is already in progress.", severity="warning")
            return
        if not test and not self._shard_ok():
            return
        if not test and not self._window_ok():
            return
        if self._on("full_repo") and test:
            self.notify("Test run is capped; untick Full repo for it.", severity="error")
            return
        repos_file = self._val("repos_file") or "repos.txt"
        if not test and not os.path.isfile(os.path.join(HERE, repos_file)) and not os.path.isfile(repos_file):
            self.notify(f"Repos file not found: {repos_file}", severity="error")
            return
        if not test:
            try:
                diffs = self._lead_differences()
            except ValueError:
                diffs = []
            if diffs:
                self._log("[tui] NOT STARTED: these settings differ from the lead's. Different flags make the merge "
                          "refuse your files and turn uploads off:", style="bold red")
                for name, lead, mine in diffs:
                    self._log(f"  --{name.replace('_', '-')}: lead {lead!r}, yours {mine!r}", style="red")
                self._log("  Press F2 with the join box empty to put the lead's settings back, or change the fields.",
                          style="yellow")
                self.notify("Your settings differ from the lead's; see the log. F2 restores them.", severity="error", timeout=10)
                return
            if self.store_state and not self.store_state.get("ok") and not self.store_state.get("unset"):
                self.notify(f"Uploads are off: {self.store_state['why']}. Everything is still saved here.",
                            severity="warning", timeout=10)
        mp = self._val("max_prs")
        self.max_prs = int(mp) if mp.isdigit() and not self._on("full_repo") else None
        self.test_mode = test
        if test:
            tp = self._val("test_prs")
            self.max_prs = int(tp) if tp.isdigit() else 10
        self.repos.clear()
        self.stats = RunStats(started=time.time())
        self.assembly = None
        self.query_one("#repos", DataTable).clear()
        fd, self.control_path = tempfile.mkstemp(prefix="riffle-control-", suffix=".txt")
        os.close(fd)
        self.skip_set = set()
        fd, self.events_path = tempfile.mkstemp(prefix="riffle-events-", suffix=".jsonl")
        os.close(fd)
        self.events_pos = 0
        self.out_paths = self._output_paths(test)
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
            self._log(f"[tui] log file: {self.log_path}  ·  trace: "
                      f"{self.log_path.rsplit('.', 1)[0] + '.trace.jsonl'}", style="cyan")
        self.run_worker(self._run_proc(cmd), exclusive=True, group="proc")
        self._set_running(True)

    def action_stop(self) -> None:
        if self.proc is None:
            return
        try:
            os.killpg(self.proc.pid, signal.SIGINT)
            self._log("[stop] SIGINT sent; the checkpoint keeps finished PRs. "
                      "Start again with 'Reuse clones' on to resume."
                      + (" The miner uploads what it has before it exits (up to two minutes)."
                         if self.store_state and self.store_state.get("ok") else ""), style="yellow")
        except ProcessLookupError:
            pass

    def action_clear_log(self) -> None:
        self.query_one("#log", Log).clear()
        self.log_lines.clear()

    async def action_quit(self) -> None:
        proc = self.proc
        if proc is not None:
            if self._quitting:                       # a second ctrl+q: do not wait for the upload
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                self.exit()
                return
            self._quitting = True
            self.action_stop()
            wait = 10.0
            if self.store_state and self.store_state.get("ok"):
                wait = float(os.environ.get("RIFFLE_SYNC_TIMEOUT", "120")) + 20
                self._log("[tui] waiting for the miner to upload before quitting (ctrl+q again to quit now)", style="yellow")
                self.notify("Uploading before quitting. ctrl+q again to quit now.", timeout=8)
            try:
                await asyncio.wait_for(proc.wait(), timeout=wait)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
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
        self.query_one("#join", Button).disabled = running
        locked = self.lead_argv is not None
        for w in self.query("#form Input, #form Checkbox, #form Select"):
            w.disabled = running or (locked and w.id in LOCKED_IDS)

    # ------------------------------------------------------------- subprocess
    async def _run_proc(self, cmd: list[str]) -> None:
        env = dict(os.environ, RIFFLE_EVENTS=self.events_path, PYTHONUNBUFFERED="1",
                   RIFFLE_CONTROL=self.control_path or "")
        if self.log_path:               # the structured trace sits next to the run log
            env["RIFFLE_TRACE"] = self.log_path.rsplit(".", 1)[0] + ".trace.jsonl"
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
                     else "yellow" if (text.startswith(("[warn", "[skip", "[llm-quota]"))
                                       or (text.startswith("[health]") and " FLAG " in text))
                     else None)
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
            self.log_path = os.path.join(LOG_DIR, naming.run_stem(self._shard()) + ".log")
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
        elif ev == "assemble":
            self.assembly = {k: v for k, v in e.items() if k not in ("t", "ev")}
        elif ev == "sync":
            self.sync_info = dict(e, t=t)
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
                elif e.get("llm") == "skipped":
                    r.llm_skip += 1
        elif ev == "llm_quota":
            self.stats.llm_paused_until = e.get("until")
            self.stats.llm_paused_why = e.get("why")
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
        if not self.query("#stats"):              # the screen is closing (a timer fired late)
            return
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
        llm_skip = sum(r.llm_skip for r in self.repos.values())
        until = getattr(self.stats, "llm_paused_until", None)
        paused = None
        if until and until > time.time() and self.proc:
            paused = (f"LLM paused for plan reset until "
                      f"{time.strftime('%H:%M', time.localtime(until))} "
                      f"({_fmt_secs(until - time.time())})")
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
        line2 = (f"LLM ok {llm_ok}  fail {llm_fail}  skipped {llm_skip}  ·  avg latency "
                 f"{f'{lat:.1f}s' if lat else '—'}{utxt}  ·  breaker "
                 + (f"[bold red]{self.stats.breaker}[/]" if self.stats.breaker else "[green]armed[/]"))
        if paused:
            line2 += f"\n[bold yellow]{paused}[/]"
        plan = self._plan_text()
        if plan:
            line2 += "\n" + plan
        line3 = ("[b]Saving to:[/b] " if self.proc else "[b]Saved to:[/b] ") + \
            "\n            ".join(self.out_paths)
        if self.log_path:
            line3 += f"\n[b]Log:[/b] {self.log_path}"
        store = self._sync_text()
        if store:
            line3 += f"\n[b]Team store:[/b] {store}"
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
            "llm_skipped": llm_skip, "llm_paused": paused,
            "llm_usage": usage,
            "breaker": self.stats.breaker,
            "assembly": self.assembly,
        })

    def _sync_text(self) -> str | None:
        """One line for the stats panel: is this run uploading, and when did it last?"""
        si = self.sync_info
        if not si:
            return None
        if si.get("off"):
            return f"[yellow]uploads OFF for this run[/] · {esc(str(si['off']))}"
        if si.get("on"):
            return f"[green]uploading as {esc(str(si['on']))}[/] · after each repo and when you stop"
        ago = _fmt_secs(max(0, time.time() - si["t"]))
        line = f"last upload {ago} ago · {si.get('sent', 0)} files sent"
        if si.get("failed"):
            line += f" · [red]{si['failed']} failed[/]"
        if si.get("stopped"):
            line += f" · [yellow]{esc(str(si['stopped']))}; the next repo retries[/]"
        return line

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
