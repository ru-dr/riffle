"""
doctor.py — check (and help fix) the miner's environment.

tui.py runs this on every launch, before the UI opens. It checks the OS, Python,
git, the Python packages, the GitHub token, the LLM setup, the
API-contract tools, and disk/RAM, then offers to fix what it can:

  - missing Python packages / semgrep: pip-installs them (one prompt)
  - GITHUB_TOKEN missing, a template placeholder, or rejected by GitHub:
    asks for a token (hidden input), checks it, and saves it to .env
    (mode 600 where the OS supports it)
  - git / Node / Claude Code / API tools missing: shows the install command for
    this OS and package manager and runs it only if you say yes

Supported: Linux (Debian/Ubuntu and derivatives, Fedora/RHEL and derivatives,
Arch, openSUSE, Alpine), macOS (Homebrew), Windows (winget, Scoop, Chocolatey)
and WSL. Anything else still gets the checks, plus a manual install hint.

The API-contract tools are required while "API contract diff" is on (the
default); each one diffs one kind of spec: oasdiff (OpenAPI), buf (protobuf),
graphql-inspector (GraphQL). A missing tool shows as a failure and is offered an
install. It does not block launch, because the miner degrades on purpose (the
column stays null, never a guess), but the TUI waits for Enter on it.

    python doctor.py            # interactive check + fixes
    python doctor.py --check    # report only, never prompts (exit 1 on problems)

Non-interactive (no TTY) runs only report, in plain text. NO_COLOR=1 turns
colour off. tui.py waits 5 s after the report before opening (RIFFLE_NO_WAIT=1 skips). RIFFLE_NO_DOCTOR=1 skips the doctor in tui.py; RIFFLE_NO_INSTALL=1
makes it report without installing anything.
"""
from __future__ import annotations

import getpass
import importlib.util
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import textwrap
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable

import deps
from version import __version__

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(HERE, ".env")
ENV_EXAMPLE = os.path.join(HERE, ".env.example")
MIN_PY = (3, 10)
# values .env.example ships with; a copied template must not count as "set"
PLACEHOLDERS = {"", "TOKEN", "KEY", "ghp_xxxxxxxxxxxx", "changeme", "your_token_here"}
TOKEN_URL = "https://github.com/settings/tokens/new?scopes=public_repo&description=riffle-miner"
# API-contract tools (needed while "API contract diff" is on, which is the default):
# tool -> (what it diffs, install docs)
OPTIONAL_TOOLS = {
    "oasdiff": ("OpenAPI specs", "https://github.com/oasdiff/oasdiff#installation"),
    "buf": ("protobuf (.proto)", "https://buf.build/docs/installation"),
    "graphql-inspector": ("GraphQL schemas", "npm i -g @graphql-inspector/cli"),
}

OK, WARN, FAIL = "ok", "warn", "fail"


@dataclass
class Result:
    name: str
    status: str
    detail: str
    fix: str | None = None        # key of the fixer to offer, if any
    group: str = "System"         # section heading in the report


# ------------------------------------------------------------------ platform
@dataclass(frozen=True)
class Platform:
    system: str       # platform.system(): Linux, Darwin, Windows
    family: str       # debian, rhel, arch, suse, alpine, macos, windows, other
    name: str         # for people: "Ubuntu 24.04.1 LTS", "macOS 15.1", "Windows 11"
    pm: str | None    # package manager found on PATH, if any
    wsl: bool
    machine: str
    root: bool


_FAMILY_IDS = {
    "debian": {"debian", "ubuntu", "linuxmint", "pop", "raspbian", "kali", "elementary",
               "zorin", "neon", "devuan"},
    "rhel": {"fedora", "rhel", "centos", "rocky", "almalinux", "amzn", "ol", "nobara"},
    "arch": {"arch", "manjaro", "endeavouros", "garuda", "cachyos"},
    "suse": {"opensuse", "opensuse-leap", "opensuse-tumbleweed", "sles", "sled", "suse"},
    "alpine": {"alpine"},
}
# package managers to look for, in order of preference
_FAMILY_PMS = {
    "debian": ["apt-get"], "rhel": ["dnf", "yum"], "arch": ["pacman"], "suse": ["zypper"],
    "alpine": ["apk"], "macos": ["brew"], "windows": ["winget", "scoop", "choco"],
    "other": ["apt-get", "dnf", "yum", "pacman", "zypper", "apk", "brew"],
}


def _linux_family(os_release: dict[str, str]) -> str:
    ids = [os_release.get("ID", "").lower(), *os_release.get("ID_LIKE", "").lower().split()]
    for i in ids:
        for fam, known in _FAMILY_IDS.items():
            if i in known or (fam == "suse" and i.startswith("opensuse")):
                return fam
    return "other"


def _read_os_release() -> dict[str, str]:
    try:
        return dict(platform.freedesktop_os_release())
    except (OSError, AttributeError):
        return {}


def detect_platform(system: str | None = None, os_release: dict[str, str] | None = None,
                    which: Callable[[str], str | None] = shutil.which,
                    root: bool | None = None, wsl: bool | None = None) -> Platform:
    """What we're running on. Arguments exist so tests can fake another OS."""
    system = system or platform.system()
    machine = platform.machine() or "unknown"
    if root is None:
        root = hasattr(os, "geteuid") and os.geteuid() == 0
    if system == "Darwin":
        family, name = "macos", f"macOS {platform.mac_ver()[0] or ''}".strip()
    elif system == "Windows":
        family, name = "windows", f"Windows {platform.release()}".strip()
    else:
        rel = _read_os_release() if os_release is None else os_release
        family = _linux_family(rel) if system == "Linux" else "other"
        name = rel.get("PRETTY_NAME") or rel.get("NAME") or system or "unknown OS"
    if wsl is None:
        wsl = system == "Linux" and (bool(os.environ.get("WSL_DISTRO_NAME"))
                                     or "microsoft" in platform.uname().release.lower())
    pm = next((p for p in _FAMILY_PMS[family] if which(p)), None)
    return Platform(system, family, name, pm, wsl, machine, root)


# ------------------------------------------------------------------- install
# tool -> package manager -> package name(s)
_PKGS = {
    "git": {"apt-get": "git", "dnf": "git", "yum": "git", "pacman": "git", "zypper": "git",
            "apk": "git", "brew": "git", "winget": "Git.Git", "scoop": "git", "choco": "git"},
    "node": {"apt-get": "nodejs npm", "dnf": "nodejs npm", "yum": "nodejs npm",
             "pacman": "nodejs npm", "zypper": "nodejs npm", "apk": "nodejs npm",
             "brew": "node", "winget": "OpenJS.NodeJS.LTS", "scoop": "nodejs-lts",
             "choco": "nodejs-lts"},
    "go": {"apt-get": "golang-go", "dnf": "golang", "yum": "golang", "pacman": "go",
           "zypper": "go", "apk": "go", "brew": "go", "winget": "GoLang.Go", "scoop": "go",
           "choco": "golang"},
    "oasdiff": {"brew": "oasdiff"},
    "buf": {"brew": "buf", "scoop": "buf"},
}
_NATIVE_CLAUDE = ("irm https://claude.ai/install.ps1 | iex" if os.name == "nt"
                  else "curl -fsSL https://claude.ai/install.sh | bash")


def _elevate(plat: Platform) -> list[str] | None:
    """Prefix for commands that need root; [] when not needed, None when impossible."""
    if plat.root or plat.system == "Windows":
        return []
    for tool in ("sudo", "doas"):
        if shutil.which(tool):
            return [tool]
    return None


def _pm_command(pm: str, pkgs: str, plat: Platform) -> list[str] | None:
    names = pkgs.split()
    if pm in ("brew", "scoop"):
        return [pm, "install", *names]
    if pm == "winget":
        return ["winget", "install", "--id", names[0], "-e",
                "--accept-package-agreements", "--accept-source-agreements"]
    if pm == "choco":
        return ["choco", "install", "-y", *names]
    sudo = _elevate(plat)
    if sudo is None:
        return None
    verbs = {"apt-get": ["install", "-y"], "dnf": ["install", "-y"], "yum": ["install", "-y"],
             "pacman": ["-S", "--noconfirm"], "zypper": ["install", "-y"], "apk": ["add"]}
    return [*sudo, pm, *verbs[pm], *names] if pm in verbs else None


def _npm_install(pkg: str, plat: Platform) -> list[str]:
    """`npm install -g`, into ~/.local when the global prefix is not writable
    (system Node on Linux puts it in /usr/local, which needs root)."""
    if shutil.which("npm"):
        prefix = deps.cmd_output(["npm", "prefix", "-g"])
        writable = bool(prefix) and os.access(prefix, os.W_OK)
    else:                           # npm comes from the package manager in this plan
        writable = plat.system != "Linux" or plat.root
    user = [] if writable else ["--prefix", os.path.join(os.path.expanduser("~"), ".local")]
    return ["npm", "install", "-g", *user, pkg]


def _chain(prereq: str, final: list[str], plat: Platform) -> list[list[str]]:
    """`final`, preceded by installing Node (npm) or Go first when it is missing."""
    if shutil.which("npm" if prereq == "node" else "go"):
        return [final]
    pre = install_plan(prereq, plat)
    return [*pre, final] if pre else []


def install_plan(tool: str, plat: Platform) -> list[list[str]]:
    """Commands (run in order) that install `tool` here; empty if we know none."""
    if tool == "venv":
        if plat.family != "debian" or not plat.pm:
            return []
        cmd = _pm_command(plat.pm, f"python{sys.version_info.major}.{sys.version_info.minor}-venv", plat)
        return [cmd] if cmd else []
    if tool == "git" and plat.family == "macos" and not plat.pm:
        return [["xcode-select", "--install"]]
    pkgs = _PKGS.get(tool, {})
    if plat.pm in pkgs:
        cmd = _pm_command(plat.pm, pkgs[plat.pm], plat)
        return [cmd] if cmd else []
    if tool in ("claude", "graphql-inspector"):
        pkg = "@anthropic-ai/claude-code" if tool == "claude" else "@graphql-inspector/cli"
        return _chain("node", _npm_install(pkg, plat), plat)
    if tool == "buf":
        if shutil.which("npm") or not shutil.which("go"):     # npm also serves graphql-inspector
            return _chain("node", _npm_install("@bufbuild/buf", plat), plat)
        return [["go", "install", "github.com/bufbuild/buf/cmd/buf@latest"]]
    if tool == "oasdiff":
        return _chain("go", ["go", "install", "github.com/oasdiff/oasdiff@latest"], plat)
    return []


def install_hint(tool: str, plat: Platform) -> str:
    """What to do by hand when install_plan has nothing."""
    if tool == "git":
        return {"macos": "run `xcode-select --install`, or install Homebrew (https://brew.sh) "
                         "and `brew install git`",
                "windows": "https://git-scm.com/download/win (or `winget install --id Git.Git -e`)",
                }.get(plat.family, "install `git` with your package manager")
    if tool == "claude":
        return f"`{_NATIVE_CLAUDE}`, then run `claude` once and /login (needed for --llm)"
    if tool in OPTIONAL_TOOLS:
        return OPTIONAL_TOOLS[tool][1]
    if tool == "venv":
        return deps.venv_package_hint()
    return "see your package manager's docs"


def _tool_path(cmd: list[str]) -> list[str]:
    """Resolve argv[0] on PATH so Windows finds npm.cmd, winget.exe and friends."""
    return [shutil.which(cmd[0]) or cmd[0], *cmd[1:]]


def _run(cmd: list[str]) -> int:
    try:
        return subprocess.run(_tool_path(cmd)).returncode
    except OSError as e:
        print(f"  could not run {cmd[0]}: {e}")
        return 127


# ------------------------------------------------------------------- display
def _color() -> bool:
    return (sys.stdout.isatty() and not os.environ.get("NO_COLOR")
            and os.environ.get("TERM") != "dumb" and _VT_OK)


def _unicode() -> bool:
    try:
        "✓✗╭─│·…".encode(sys.stdout.encoding or "ascii")
        return True
    except (UnicodeEncodeError, LookupError):
        return False


def _enable_windows_vt() -> bool:
    """Turn on ANSI escapes in the Windows console (Windows 10+)."""
    if os.name != "nt":
        return True
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        handle, mode = k32.GetStdHandle(-11), ctypes.c_ulong()
        return bool(k32.GetConsoleMode(handle, ctypes.byref(mode))
                    and k32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        return False


_VT_OK = _enable_windows_vt()
_CODES = {"bold": "1", "dim": "2", "red": "31", "green": "32", "yellow": "33", "cyan": "36"}


def _c(text: str, *styles: str) -> str:
    if not _color():
        return text
    return f"\033[{';'.join(_CODES[s] for s in styles)}m{text}\033[0m"


def _sym(status: str) -> str:
    """One status mark: coloured glyph on a terminal, plain label in logs."""
    if not sys.stdout.isatty():
        return {OK: "OK  ", WARN: "WARN", FAIL: "FAIL"}[status]
    glyph = ({OK: "✓", WARN: "!", FAIL: "✗"} if _unicode() else {OK: "+", WARN: "!", FAIL: "x"})[status]
    return _c(glyph, {OK: "green", WARN: "yellow", FAIL: "red"}[status], "bold")


def _width() -> int:
    return max(50, min(shutil.get_terminal_size((80, 24)).columns, 88))


def _header(plat: Platform) -> None:
    sub = " · ".join([plat.name + (" (WSL)" if plat.wsl else ""), plat.machine,
                      plat.pm or "no package manager",
                      f"Python {sys.version_info.major}.{sys.version_info.minor}"])
    if not sys.stdout.isatty():
        print(f"Riffle doctor v{__version__} — {sub}")
        return
    uni = _unicode()
    tl, tr, bl, br, h, v = "╭╮╰╯─│" if uni else "++++-|"
    inner = _width() - 4
    title = "Riffle doctor"
    ver = f"miner v{__version__}"
    sub = sub if len(sub) <= inner else sub[:inner - 1] + ("…" if uni else ".")
    pad = " " * max(1, inner - len(title) - len(ver))
    print(_c(tl + h * (inner + 2) + tr, "cyan"))
    print(f"{_c(v, 'cyan')} {_c(title, 'bold')}{pad}{_c(ver, 'dim')} {_c(v, 'cyan')}")
    print(f"{_c(v, 'cyan')} {_c(sub.ljust(inner), 'dim')} {_c(v, 'cyan')}")
    print(_c(bl + h * (inner + 2) + br, "cyan"))


def _report(results: list[Result]) -> None:
    w = max(len(r.name) for r in results)
    mark = 1 if sys.stdout.isatty() else 4          # visible width of _sym()
    indent = 2 + mark + 1 + w + 2
    room = max(20, _width() - indent)
    groups: dict[str, list[Result]] = {}
    for r in results:
        groups.setdefault(r.group, []).append(r)
    for group, rows in groups.items():
        print("\n" + _c(group, "bold"))
        for r in rows:
            lines = textwrap.wrap(r.detail, room) or [""]
            print(f"  {_sym(r.status)} {r.name.ljust(w)}  {lines[0]}")
            for extra in lines[1:]:
                print(" " * indent + extra)


def _summary(results: list[Result]) -> None:
    n = {s: sum(r.status == s for r in results) for s in (OK, WARN, FAIL)}
    print("\n" + "   ".join([_c(f"{n[OK]} ok", "green"),
                             _c(f"{n[WARN]} warning" + ("" if n[WARN] == 1 else "s"), "yellow"),
                             _c(f"{n[FAIL]} failed", "red")]))


def _ask(prompt: str, default: bool = True) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    try:
        ans = input(f"  {_c('?', 'cyan', 'bold')} {prompt} {hint} ").strip().lower()
    except EOFError:
        return default
    return default if not ans else ans in ("y", "yes")


# --------------------------------------------------------------------- state
def _read_env_file() -> dict[str, str]:
    """KEY=VALUE pairs from .env, parsed the same way config.py does."""
    out: dict[str, str] = {}
    try:
        with open(ENV_PATH, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def _env(key: str) -> str:
    """The value config.py will use: a real env var wins over .env."""
    return os.environ.get(key) or _read_env_file().get(key, "")


def _set_env_value(key: str, value: str) -> None:
    """Write KEY=value into .env (created from .env.example if missing),
    replacing an existing KEY line, and make the file private (600)."""
    if os.path.isfile(ENV_PATH):
        with open(ENV_PATH, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    elif os.path.isfile(ENV_EXAMPLE):
        with open(ENV_EXAMPLE, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    else:
        lines = []
    for i, line in enumerate(lines):
        if line.strip().partition("=")[0].strip() == key:
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    fd = os.open(ENV_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    try:
        os.chmod(ENV_PATH, 0o600)
    except OSError:
        pass
    os.environ[key] = value                     # take effect in this process too


def _github_token_status(token: str) -> tuple[str, str]:
    """(status, detail) for a token, by asking GitHub's /rate_limit (free call)."""
    if token in PLACEHOLDERS:
        return FAIL, "not set" if not token else f"still the template placeholder ({token!r})"
    req = urllib.request.Request(
        "https://api.github.com/rate_limit",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "User-Agent": "riffle-doctor"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            core = json.loads(r.read()).get("resources", {}).get("core", {})
            return OK, f"valid · {core.get('remaining', '?')}/{core.get('limit', '?')} API calls left"
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return FAIL, "rejected by GitHub (expired or revoked)"
        return WARN, f"could not verify (HTTP {e.code})"
    except (urllib.error.URLError, OSError, ValueError):
        return WARN, "set, but could not reach GitHub to verify (offline?)"


def _tool_version(exe: str, *args: str) -> str | None:
    """None if `exe` isn't on PATH, else its version line ('' if it won't say)."""
    path = shutil.which(exe)
    if not path:
        return None
    try:
        p = subprocess.run([path, *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    lines = (p.stdout if p.returncode == 0 else "").strip().splitlines()
    return lines[0][:60] if lines else ""


def _total_ram() -> int | None:
    try:
        import psutil
        return psutil.virtual_memory().total
    except ImportError:
        pass
    try:
        if sys.platform.startswith("linux"):
            with open("/proc/meminfo", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("MemTotal:"):
                        return int(line.split()[1]) * 1024
        elif sys.platform == "darwin":
            out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True,
                                 text=True, timeout=5).stdout
            return int(out.strip())
        elif os.name == "nt":
            import ctypes

            class _MemStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            *((n, ctypes.c_ulonglong) for n in (
                                "ullTotalPhys", "ullAvailPhys", "ullTotalPageFile",
                                "ullAvailPageFile", "ullTotalVirtual", "ullAvailVirtual",
                                "ullAvailExtendedVirtual"))]
            st = _MemStatus()
            st.dwLength = ctypes.sizeof(st)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return st.ullTotalPhys
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return None


def _venv_hint(plat: Platform) -> str:
    return deps.venv_package_hint()


def _path_note(tool: str, added: list[str]) -> str:
    """Suffix for a tool found only in a user bin dir that is not on PATH."""
    path = shutil.which(tool)
    d = os.path.dirname(path) if path else ""
    return (f" · {d} is not on your PATH (added for this run; add it to your shell profile)"
            if d and d in added else "")


# -------------------------------------------------------------------- checks
def _team_store_check() -> tuple[str, str, str, str | None]:
    """The join token (the write token inside the lead's join code): (name, status, detail, fix). Optional, so a
    problem is a warning, never a failure."""
    import sync
    url, token = _env("RIFFLE_SYNC_URL"), _env("RIFFLE_SYNC_TOKEN")
    if not url or not token:
        return ("Join token", WARN, "not joined: your output stays on this PC. Paste the join code the lead sent you",
                "join")
    r = sync.whoami(url, token)
    if r["ok"]:
        return ("Join token", OK, f"{r['name']} · split {r.get('split') or 'any'} · output is sent after each repo", None)
    return ("Join token", WARN, f"{r['why']}. The run still works and keeps everything locally; "
                                f"paste a new join code from the lead", "join")


def _read_token_check() -> tuple[str, str, str, str | None]:
    """The read token: signs in to the store's pages (never used by the miner itself)."""
    import sync
    url, token = _env("RIFFLE_SYNC_URL"), _env("RIFFLE_READ_TOKEN")
    if not url:
        return ("Read token", WARN, "needs the store address: join first (the join code carries it)", None)
    if not token:
        return ("Read token", WARN, f"optional: lets you open {url} and read the mined data. The lead makes one on "
                                    f"the Admin page", "read")
    r = sync.whoami(url, token)
    if r.get("kind") == "readers":
        return ("Read token", OK, f"{r['name']} · open {url} and paste it to sign in", None)
    why = r["why"] if not r.get("kind") else f"that is a {str(r['kind']).rstrip('s')} token, not a read token"
    return ("Read token", WARN, f"{why}", "read")


def _admin_token_check() -> tuple[str, str, str, str | None] | None:
    """The lead's admin token; only shown when one is set (volunteers have none)."""
    import sync
    url, token = _env("RIFFLE_SYNC_URL"), _env("RIFFLE_ADMIN_TOKEN")
    if not token:
        return None
    r = sync.whoami(url, token) if url else {"ok": False, "why": "needs RIFFLE_SYNC_URL"}
    if r.get("kind") == "admins":
        return ("Admin token", OK, f"{r['name']} · can issue and revoke PC tokens (TUI Lead tools, F3)", None)
    return ("Admin token", WARN, r["why"] if not r.get("kind") else f"that is a {str(r['kind']).rstrip('s')} token, not an admin token", None)


def run_checks(plat: Platform | None = None) -> list[Result]:
    plat = plat or detect_platform()
    added = deps.extend_path()      # tools installed to ~/go/bin, ~/.local/bin, npm -g count
    res: list[Result] = []

    def add(name: str, status: str, detail: str, fix: str | None = None,
            group: str = "System") -> None:
        res.append(Result(name, status, detail, fix, group))

    add("OS", OK, " · ".join([plat.name + (" (WSL)" if plat.wsl else ""), plat.machine,
                              f"packages via {plat.pm}" if plat.pm else "no package manager found"]))
    if plat.family == "macos" and not plat.pm:
        add("Homebrew", WARN, "not found; easiest way to install tools: https://brew.sh")
    if plat.wsl and HERE.startswith("/mnt/"):
        add("WSL path", WARN, "repo is on the Windows drive (/mnt/…): clones are slow here; "
                              "move it into the Linux home directory")

    py = sys.version_info
    add("Python", OK if py >= MIN_PY else FAIL,
        f"{py.major}.{py.minor}.{py.micro}"
        + ("" if py >= MIN_PY else f" (need {MIN_PY[0]}.{MIN_PY[1]}+)"))

    # Where packages go. Ubuntu/Debian 23.04+ (PEP 668) block pip outside a
    # venv, so there they go into repo_miner/.venv, which needs python3-venv.
    if deps.in_venv():
        where = "project .venv" if os.path.abspath(sys.prefix) == os.path.abspath(deps.VENV_DIR) \
            else f"virtualenv {sys.prefix}"
        add("Environment", OK, where)
    elif deps.externally_managed():
        if importlib.util.find_spec("ensurepip") is None:
            add("Environment", FAIL, "system Python is externally managed (PEP 668) and the venv "
                                     f"module is missing: {_venv_hint(plat)}", fix="venv")
        else:
            add("Environment", OK, "system Python is externally managed; packages go in .venv")
    else:
        add("Environment", OK, "system Python (pip --user)")

    git = shutil.which("git")
    if not git:
        add("git", FAIL, "not found on PATH (the miner needs it)", fix="git")
    else:
        ver = _tool_version("git", "--version")
        if ver is None or not ver:          # e.g. the macOS stub before Xcode tools are installed
            add("git", FAIL, "found but does not run"
                             + (" (macOS: run `xcode-select --install`)" if plat.family == "macos" else ""),
                fix="git")
        else:
            add("git", OK, ver.replace("git version ", ""))
            if plat.system == "Windows":    # deep repos exceed the 260-char path limit
                lp = deps.cmd_output(["git", "config", "--get", "core.longpaths"])
                add("git long paths", OK if lp == "true" else WARN,
                    "enabled" if lp == "true" else
                    "off: clones of repos with deep paths can fail on Windows", fix=None if lp == "true" else "longpaths")

    need = deps.missing()
    add("Python packages", OK if not need else FAIL,
        "all installed" if not need else "missing: " + ", ".join(need),
        fix="pip" if need else None, group="Packages")
    if plat.system == "Windows" and shutil.which("semgrep") is None:
        add("semgrep", WARN, "no stable native Windows build; semgrep columns stay null "
                             "(use WSL for them)", group="Packages")

    tok = _env("GITHUB_TOKEN")
    st, detail = _github_token_status(tok)
    add("GITHUB_TOKEN", st, detail, fix="token" if st == FAIL else None, group="Credentials")

    for chk in (_team_store_check(), _read_token_check(), _admin_token_check()):
        if chk:
            add(chk[0], chk[1], chk[2], fix=chk[3], group="Credentials")

    backend = _env("LLM_BACKEND") or "claude_cli"
    if backend == "claude_cli":
        ver = _tool_version("claude", "--version")
        if ver is not None:
            add("claude CLI", OK, f"{ver or 'found'} · use Test LLM (ctrl+t) to check login"
                + _path_note("claude", added), group="LLM")
        else:
            add("claude CLI", WARN, "`claude` not found: the LLM flags option won't work",
                fix="claude", group="LLM")
    else:
        key = _env("LLM_API_KEY")
        has_sdk = importlib.util.find_spec("openai") is not None
        bad = [m for m, cond in (("LLM_API_KEY not set", key in PLACEHOLDERS),
                                 ("openai package missing", not has_sdk)) if cond]
        add(f"LLM ({backend})", WARN if bad else OK,
            "; ".join(bad) or "LLM_API_KEY set, openai installed",
            fix="llm_key" if key in PLACEHOLDERS else ("openai" if not has_sdk else None),
            group="LLM")

    group = "API contract diff (required)"
    fix_key: str | None = "api_tools"        # one prompt installs every missing tool
    for tool, (what, _) in OPTIONAL_TOOLS.items():
        ver = _tool_version(tool, "--version")
        if ver is not None:
            add(tool, OK, (f"{what} · {ver}" if ver else f"{what} · found")
                + _path_note(tool, added), group=group)
        else:
            add(tool, FAIL, f"{what} · not installed: `api_contract_change` stays null for these specs",
                fix=fix_key, group=group)
            fix_key = None

    try:
        free = shutil.disk_usage(HERE).free
        add("Disk free", OK if free >= 20 * 2**30 else WARN,
            f"{free / 2**30:.0f} GB" + ("" if free >= 20 * 2**30 else
                                        " (big repos need 5-20 GB each to clone)"), group="Resources")
    except OSError:
        pass
    ram = _total_ram()
    if ram:
        add("RAM", OK if ram >= 8 * 2**30 else WARN,
            f"{ram / 2**30:.0f} GB" + ("" if ram >= 8 * 2**30 else
                                       " (lower PR workers on large repos)"), group="Resources")
    return res


# --------------------------------------------------------------------- fixes
def _fix_pip(plat: Platform) -> None:
    if os.getenv("RIFFLE_NO_INSTALL"):
        print("  RIFFLE_NO_INSTALL is set; skipping install.")
        return
    deps.ensure(require_textual=False)


def _fix_token(plat: Platform) -> None:
    print(f"  Create a token (scope: public_repo) at:\n    {TOKEN_URL}")
    for _ in range(3):
        try:
            tok = getpass.getpass("  Paste token (hidden, Enter to skip): ").strip()
        except EOFError:
            return
        if not tok:
            return
        st, detail = _github_token_status(tok)
        if st == FAIL:
            print(f"  {_sym(FAIL)} {detail}; try again.")
            continue
        _set_env_value("GITHUB_TOKEN", tok)
        where = "private, gitignored" if os.name != "nt" else "gitignored"
        print(f"  {_sym(st)} {detail} · saved to {ENV_PATH} ({where})")
        return


def _fix_join(plat: Platform) -> None:
    """Paste the lead's join code: check it with the store, save it, and say what to run."""
    import join
    import sync
    for _ in range(3):
        try:
            code = getpass.getpass("  Paste the join code (hidden, Enter to skip): ").strip()
        except EOFError:
            return
        if not code:
            return
        try:
            doc = join.decode(code)
        except ValueError as e:
            print(f"  {_sym(FAIL)} {e}; try again.")
            continue
        r = sync.whoami(doc["u"], doc["t"])
        if not r["ok"]:
            print(f"  {_sym(FAIL)} {r['why']}. Ask the lead for a new code.")
            return
        base = os.path.dirname(ENV_PATH)
        try:
            doc = join.apply_split(doc, sync.fetch_split(doc["u"], doc["t"]), base)      # the lead's repo list, not our own file
        except OSError as e:
            print(f"  {_sym(WARN)} could not save the lead's repo list ({e}); the code's own flags stand.")
        join.write_env(ENV_PATH, {"RIFFLE_SYNC_URL": doc["u"], "RIFFLE_SYNC_TOKEN": doc["t"]})
        join.save_state(os.path.join(base, join.STATE_FILE), doc)
        os.environ["RIFFLE_SYNC_URL"], os.environ["RIFFLE_SYNC_TOKEN"] = doc["u"], doc["t"]
        print(f"  {_sym(OK)} joined as {r['name']} (PC {doc['k']} of {doc['n']}) · saved to {ENV_PATH}")
        print(f"  Run this, exactly: {join.command(doc)}   (or open the TUI: it fills the form from the code)")
        return


def _fix_read(plat: Platform) -> None:
    """Save a read token so the TUI can copy it and open the store's pages."""
    import sync
    url = _env("RIFFLE_SYNC_URL")
    if not url:
        print(f"  {_sym(WARN)} join first: the store address comes with the join code")
        return
    for _ in range(3):
        try:
            tok = getpass.getpass("  Paste the read token (starts with rd_; hidden, Enter to skip): ").strip()
        except EOFError:
            return
        if not tok:
            return
        r = sync.whoami(url, tok)
        if r.get("kind") != "readers":
            why = r["why"] if not r.get("kind") else f"that is a {str(r['kind']).rstrip('s')} token, not a read token"
            print(f"  {_sym(FAIL)} {why}; try again.")
            continue
        _set_env_value("RIFFLE_READ_TOKEN", tok)
        print(f"  {_sym(OK)} read token for {r['name']} saved to {ENV_PATH}. Open {url} and paste it to sign in "
              f"(in the TUI: F4 copies it and opens the page).")
        return


def _fix_llm_key(plat: Platform) -> None:
    try:
        key = getpass.getpass("  Paste LLM_API_KEY (hidden, Enter to skip): ").strip()
    except EOFError:
        return
    if key:
        _set_env_value("LLM_API_KEY", key)
        print(f"  saved to {ENV_PATH}")


def _fix_openai(plat: Platform) -> None:
    if not deps.in_venv() and deps.externally_managed():
        if not os.path.isfile(deps.venv_python()):
            ok, err = deps.create_venv()
            if not ok:
                print(f"  could not create .venv: {err[-200:]}\n  {_venv_hint(plat)}")
                return
        subprocess.run([deps.venv_python(), "-m", "pip", "install", "openai>=1.0"])
        return
    subprocess.run([sys.executable, "-m", "pip", "install",
                    *([] if deps.in_venv() else ["--user"]), "openai>=1.0"])


def _fix_system(tool: str, plat: Platform) -> bool | None:
    """Offer the install command for `tool`. True: ran and succeeded, False: ran and
    failed, None: nothing to run (no installer here, or you said no)."""
    plan = install_plan(tool, plat)
    if not plan:
        print(f"  No automatic installer for {tool} here: {install_hint(tool, plat)}")
        return None
    shown = " && ".join(" ".join(c) for c in plan)
    if not _ask(f"Run `{shown}`?", default=False):
        print(f"  Skipped. To install later: {shown}")
        return None
    for cmd in plan:
        rc = _run(cmd)
        if rc != 0:
            print(f"  {_sym(FAIL)} `{' '.join(cmd)}` exited {rc}\n  Install it by hand: "
                  f"{install_hint(tool, plat)}")
            return False
    added = deps.extend_path()
    if (tool == "git" or tool == "claude" or tool in OPTIONAL_TOOLS) and shutil.which(tool) is None:
        print(f"  Installed, but `{tool}` is not on PATH yet: open a new terminal"
              + (" (Windows refreshes PATH there)" if plat.system == "Windows" else
                 ", or add its bin directory to PATH") + ".")
    elif added:
        print(f"  Added {', '.join(added)} to PATH for this run; add it to your shell profile to keep it.")
    return True


def _fix_venv(plat: Platform) -> None:
    if _fix_system("venv", plat) is False and plat.pm:      # older releases name it python3-venv
        cmd = _pm_command(plat.pm, "python3-venv", plat)
        if cmd:
            _run(cmd)


def _fix_api_tools(plat: Platform) -> None:
    for tool in OPTIONAL_TOOLS:
        if shutil.which(tool) is None:
            _fix_system(tool, plat)


def _fix_longpaths(plat: Platform) -> None:
    _run(["git", "config", "--global", "core.longpaths", "true"])
    print("  Set git config --global core.longpaths true")


FIXERS: dict[str, tuple[str, Callable[[Platform], None]]] = {
    "venv": ("Install the Python venv module now?", _fix_venv),
    "pip": ("Install the missing Python packages now?", _fix_pip),
    "token": ("Set up a GitHub token now?", _fix_token),
    "join": ("Paste the lead's join code now?", _fix_join),
    "read": ("Save a read token for the store's pages now?", _fix_read),
    "llm_key": ("Set LLM_API_KEY now?", _fix_llm_key),
    "openai": ("Install the openai package now?", _fix_openai),
    "git": ("Install git now?", lambda p: _fix_system("git", p)),
    "claude": ("Install Claude Code (for LLM flags) now?", lambda p: _fix_system("claude", p)),
    "api_tools": ("Install the missing API-contract tools now?", _fix_api_tools),
    "longpaths": ("Enable git long paths now?", _fix_longpaths),
}


# ---------------------------------------------------------------------- main
def countdown(seconds: int = 5) -> bool:
    """Pause before the TUI opens so the report can be read. Enter opens now.
    Returns False if cancelled with Ctrl+C. Skipped (True) without a terminal."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()) or os.getenv("RIFFLE_NO_WAIT"):
        return True
    try:
        for left in range(seconds, 0, -1):
            sys.stdout.write(f"\r  {_c('Opening the miner TUI in', 'dim')} {_c(str(left), 'bold')}"
                             f"{_c('s…  (Enter: now, Ctrl+C: cancel)', 'dim')}   ")
            sys.stdout.flush()
            if _key_pressed(1.0):
                break
        sys.stdout.write("\r" + " " * 70 + "\r")
        return True
    except KeyboardInterrupt:
        sys.stdout.write("\n")
        return False


def _key_pressed(timeout: float) -> bool:
    """Wait up to `timeout` s; True if the user pressed Enter (a key on Windows)."""
    if os.name == "nt":
        import msvcrt
        import time
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if msvcrt.kbhit():
                msvcrt.getwch()
                return True
            time.sleep(0.05)
        return False
    import select
    try:
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
    except (OSError, ValueError):
        import time
        time.sleep(timeout)
        return False
    if ready:
        sys.stdin.readline()
    return bool(ready)


def _safe_checks(plat: Platform) -> list[Result]:
    """run_checks that never takes the launcher down: a bug here becomes a warning."""
    try:
        return run_checks(plat)
    except Exception as e:        # noqa: BLE001 (fail open, like the rest of Riffle)
        return [Result("doctor", WARN, f"internal error, checks skipped: {type(e).__name__}: {e}")]


_VERSION_RE = re.compile(r'__version__\s*=\s*"([^"]+)"')


def auto_update(root: str | None = None, version_file: str | None = None, reexec: bool = True, say=print) -> str:
    """Keep this checkout on the latest `main`, so every PC mines with the same miner version.

    Runs once per launch (the TUI, `doctor.py` and `build_dataset.py` all call it; RIFFLE_UPDATE_CHECKED stops a child
    process from checking again). It fetches origin/main and fast-forwards, and only when that is safe: on `main`, no
    uncommitted changes to tracked files, and the checkout is simply behind. Anything else is reported, never forced, so
    nobody's work is touched. After an update it restarts the program once (RIFFLE_NO_AUTO_UPDATE=1 turns all of this off).
    Returns off, no-git, offline, current, updated or blocked."""
    if os.environ.get("RIFFLE_NO_AUTO_UPDATE") or os.environ.get("RIFFLE_UPDATE_CHECKED"):
        return "off"
    os.environ["RIFFLE_UPDATE_CHECKED"] = "1"
    git = shutil.which("git")
    if not git:
        return "no-git"

    def g(*args: str, timeout: int = 20) -> subprocess.CompletedProcess:
        return subprocess.run([git, "-C", root or HERE, *args], capture_output=True, text=True, timeout=timeout)
    try:
        top = g("rev-parse", "--show-toplevel")
        if top.returncode != 0:
            return "no-git"                                     # a zip download, not a checkout: nothing to update
        top_dir = top.stdout.strip()
        rel = os.path.relpath(version_file or os.path.join(HERE, "version.py"), top_dir).replace(os.sep, "/")
        if g("fetch", "--quiet", "origin", "main", timeout=30).returncode != 0:
            return "offline"
        local, remote = g("rev-parse", "HEAD").stdout.strip(), g("rev-parse", "origin/main").stdout.strip()
        if not local or not remote or local == remote:
            return "current"
        if g("merge-base", "--is-ancestor", local, remote).returncode != 0:
            return "current"                                    # ahead of main or diverged: a developer's checkout, leave it

        def version_at(ref: str) -> str:
            m = _VERSION_RE.search(g("show", f"{ref}:{rel}").stdout)
            return m.group(1) if m else "?"
        old, new = version_at("HEAD"), version_at("origin/main")
        branch = g("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        dirty = g("status", "--porcelain", "--untracked-files=no").stdout.strip()
        if branch != "main" or dirty:
            why = f"it is on branch {branch}, not main" if branch != "main" else "it has uncommitted changes"
            say(f"[update] main has a newer miner ({new}, you have {old}) but this checkout was not updated: {why}. "
                f"Run `git checkout main && git pull` yourself, or set RIFFLE_NO_AUTO_UPDATE=1 to stop this message.")
            return "blocked"
        if g("merge", "--ff-only", "origin/main", timeout=60).returncode != 0:
            say(f"[update] main has a newer miner ({new}) but it could not be applied; run `git pull` yourself.")
            return "blocked"
        say(f"[update] miner {old} -> {new} (fast-forwarded main)" + ("; restarting" if reexec else ""))
        if reexec:
            sys.stdout.flush()
            os.environ["RIFFLE_UPDATE_CHECKED"] = "1"           # the restarted process does not check again
            os.execv(sys.executable, [sys.executable, *sys.argv])
        return "updated"
    except (OSError, subprocess.SubprocessError):
        return "offline"


def run(interactive: bool | None = None) -> tuple[bool, list[Result]]:
    """Check, offer fixes, re-check. Returns (usable, final results); usable is
    False if a required check still fails (tui.py then exits instead of opening
    a broken UI)."""
    if interactive is None:
        interactive = sys.stdin.isatty() and sys.stdout.isatty()
    auto_update()                                              # first: everything below should check the newest miner
    plat = detect_platform()
    _header(plat)
    results = _safe_checks(plat)
    _report(results)
    todo = [r for r in results if r.fix and r.status in (FAIL, WARN)]
    if todo and interactive:
        print()
        asked: set[str] = set()
        try:
            for r in todo:
                if r.fix in asked:
                    continue
                asked.add(r.fix)
                question, fixer = FIXERS[r.fix]
                prompt = question if r.fix == "api_tools" else f"{r.name}: {question}"
                if _ask(prompt, default=r.status == FAIL or r.fix == "join"):
                    try:
                        fixer(plat)
                    except Exception as e:      # noqa: BLE001 (one bad fix must not stop the rest)
                        print(f"  {_sym(FAIL)} fix failed: {type(e).__name__}: {e}")
        except KeyboardInterrupt:
            print("\n  Interrupted; skipping the remaining fixes.")
        print("\n" + _c("Re-checking…", "dim"))
        results = _safe_checks(plat)
        _report(results)
    _summary(results)
    failed = [r for r in results if r.status == FAIL]
    if failed:
        print(", ".join(r.name for r in failed)
              + (" still needs" if len(failed) == 1 else " still need") + " attention"
              + ("" if interactive else " (run `python doctor.py` to fix interactively)"))
    elif any(r.status == WARN for r in results):
        print("Ready (warnings above are optional features).")
    else:
        print(_c("All good.", "green", "bold"))
    # only these make the miner/TUI unusable; a missing token or LLM still runs
    blocking = {"Python", "Environment", "git", "Python packages"}
    return not any(r.name in blocking for r in failed), results


if __name__ == "__main__":
    deps.use_project_venv()          # check the environment the TUI will really use
    check_only = "--check" in sys.argv[1:]
    usable, final = run(interactive=False if check_only else None)
    if check_only:      # strict: any failure (token too) is a non-zero exit
        sys.exit(0 if not any(r.status == FAIL for r in final) else 1)
    sys.exit(0 if usable else 1)
