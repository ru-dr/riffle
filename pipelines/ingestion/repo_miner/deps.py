"""
deps.py — install the miner's Python dependencies on first run.

tui.py calls ensure() before importing textual, so `python tui.py` on a fresh
machine installs what it needs. Python packages are pip-installed (with
--user outside a virtualenv); tools pip can't provide (git, claude) are only
checked and reported. Set RIFFLE_NO_INSTALL=1 to skip installing.

Externally managed Pythons (Ubuntu/Debian 23.04+, PEP 668) refuse
`pip install --user`. There the packages go into a private virtualenv at
repo_miner/.venv instead, created on first need; every later launch re-runs
itself inside it (child miners inherit it through sys.executable), so nothing
is installed system-wide and no flags like --break-system-packages are used.
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import shlex
import shutil
import site
import subprocess
import sys
import sysconfig

HERE = os.path.dirname(os.path.abspath(__file__))
VENV_DIR = os.path.join(HERE, ".venv")

# import name -> pip spec (kept in step with requirements.txt). Each is bounded below the next
# major version, so a fresh install cannot pick up a release with breaking changes (semgrep and
# lizard change what the findings columns mean). Raise a bound after testing against the new major.
PY_PACKAGES = {
    "textual": "textual>=8.0,<9",
    "pandas": "pandas>=2.0,<4",
    "pyarrow": "pyarrow>=14.0,<26",
    "lizard": "lizard>=1.17,<2",
    "psutil": "psutil>=5.9,<8",           # TUI RAM/CPU readout
    "segno": "segno>=1.6,<2",             # TUI QR code for the web view (pure Python)
}
# CLI tools pip can install -> pip spec
PIP_CLIS = {"semgrep": "semgrep>=1.0,<2"}
# CLI tools the user must install themselves -> how
SYSTEM_CLIS = {
    "git": "install git with your package manager",
    "claude": "install Claude Code, then run `claude` once and /login (needed for --llm)",
}


def pip_clis() -> dict[str, str]:
    """semgrep has no stable native Windows build; there its columns stay null (use WSL)."""
    return {} if os.name == "nt" else PIP_CLIS


def _all_specs() -> list[str]:
    """Everything, for a fresh venv (it starts empty, so install the lot)."""
    return [*PY_PACKAGES.values(), *pip_clis().values()]


def missing() -> list[str]:
    specs = [spec for mod, spec in PY_PACKAGES.items()
             if importlib.util.find_spec(mod) is None]
    specs += [spec for exe, spec in pip_clis().items() if shutil.which(exe) is None]
    return specs


def in_venv() -> bool:
    return sys.prefix != sys.base_prefix


def externally_managed() -> bool:
    """PEP 668: the distro marks its Python so pip won't install into it."""
    try:
        return os.path.isfile(os.path.join(sysconfig.get_paths()["stdlib"], "EXTERNALLY-MANAGED"))
    except (KeyError, OSError):
        return False


def venv_python(venv: str = VENV_DIR) -> str:
    sub = ("Scripts", "python.exe") if os.name == "nt" else ("bin", "python")
    return os.path.join(venv, *sub)


def _reexec_in(venv: str) -> None:
    """Replace this process with the same command under the venv's Python."""
    py = venv_python(venv)
    bindir = os.path.dirname(py)
    os.environ["VIRTUAL_ENV"] = venv
    os.environ["PATH"] = bindir + os.pathsep + os.environ.get("PATH", "")   # venv CLIs (semgrep)
    os.environ["RIFFLE_IN_VENV"] = "1"                                      # no exec loops
    print(f"[deps] using the project virtualenv: {venv}", flush=True)
    sys.stdout.flush(); sys.stderr.flush()
    os.execv(py, [py, *sys.orig_argv[1:]])          # same flags and script as now


def use_project_venv() -> None:
    """If repo_miner/.venv exists and we aren't in a venv, switch into it."""
    if (not in_venv() and not os.getenv("RIFFLE_IN_VENV") and not os.getenv("RIFFLE_NO_VENV")
            and os.path.isfile(venv_python())):
        _reexec_in(VENV_DIR)


def create_venv() -> tuple[bool, str]:
    """Create repo_miner/.venv with pip in it. Returns (ok, error text). Tries the
    stdlib venv, then virtualenv if installed. On Debian/Ubuntu the first fails
    without the python3-venv package (no ensurepip). --clear replaces a stale
    venv, e.g. one whose Python was upgraded away."""
    attempts = [[sys.executable, "-m", "venv", "--clear", VENV_DIR]]
    if importlib.util.find_spec("virtualenv") is not None:
        attempts.append([sys.executable, "-m", "virtualenv", VENV_DIR])
    err = ""
    for cmd in attempts:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if proc.returncode == 0 and os.path.isfile(venv_python()):
                pip = subprocess.run([venv_python(), "-m", "pip", "--version"],
                                     capture_output=True, text=True, timeout=60)
                if pip.returncode == 0:
                    return True, ""
                err = "the new virtualenv has no pip"
            else:
                err = (proc.stderr or proc.stdout).strip()
        except (OSError, subprocess.SubprocessError) as e:
            err = str(e)
        shutil.rmtree(VENV_DIR, ignore_errors=True)   # a half-made venv would be re-entered
    return False, err


def venv_package_hint() -> str:
    v = f"{sys.version_info.major}.{sys.version_info.minor}"
    if shutil.which("apt-get"):
        return f"sudo apt install python3-venv  (or python{v}-venv)"
    if shutil.which("apk"):
        return "sudo apk add python3 py3-pip"
    return ("install the venv/ensurepip module for this Python (some distros ship it as a "
            "separate package), or use a Python from python.org, pyenv or uv")


def cmd_output(cmd: list[str]) -> str:
    """stdout of a quick command, '' if it is missing, fails or hangs."""
    path = shutil.which(cmd[0])
    if not path:
        return ""
    try:
        p = subprocess.run([path, *cmd[1:]], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return p.stdout.strip() if p.returncode == 0 else ""


def extra_bin_dirs() -> list[str]:
    """Where user-level installs put binaries (go install, npm --prefix ~/.local,
    npm -g) that are often missing from PATH."""
    home = os.path.expanduser("~")
    dirs = [os.path.join(home, ".local", "bin"), os.path.join(home, "go", "bin"),
            os.environ.get("GOBIN", "")]
    gopath = cmd_output(["go", "env", "GOPATH"])
    if gopath:
        dirs.append(os.path.join(gopath.split(os.pathsep)[0], "bin"))
    prefix = cmd_output(["npm", "prefix", "-g"])
    if prefix:
        dirs.append(prefix if os.name == "nt" else os.path.join(prefix, "bin"))
    return [d for d in dirs if d]


def extend_path() -> list[str]:
    """Append existing user bin dirs that are not on PATH to this process's PATH
    (child miners inherit it). Returns the dirs added."""
    have = os.environ.get("PATH", "").split(os.pathsep)
    added: list[str] = []
    for d in extra_bin_dirs():
        if os.path.isdir(d) and d not in have and d not in added:
            added.append(d)
    if added:
        os.environ["PATH"] = os.pathsep.join([*have, *added])
    return added


def ensure(require_textual: bool = True) -> None:
    use_project_venv()
    need = missing()
    if need and not os.getenv("RIFFLE_NO_INSTALL") and not in_venv() and externally_managed():
        print("[deps] this Python is externally managed (PEP 668); installing into a "
              f"project virtualenv at {VENV_DIR}", flush=True)
        ok, err = create_venv()
        if not ok:
            print(f"[deps] could not create the virtualenv: {err.splitlines()[-1] if err else 'unknown error'}\n"
                  f"[deps] install the venv module, then run again:  {venv_package_hint()}",
                  file=sys.stderr, flush=True)
            if require_textual:
                sys.exit(1)
            return
        rc = subprocess.run([venv_python(), "-m", "pip", "install", "--upgrade", "pip", "-q"]).returncode
        rc = subprocess.run([venv_python(), "-m", "pip", "install", *_all_specs()]).returncode
        if rc != 0:
            print(f"[deps] pip exited {rc} inside {VENV_DIR}", file=sys.stderr)
        _reexec_in(VENV_DIR)
    if need and not os.getenv("RIFFLE_NO_INSTALL"):
        cmd = [sys.executable, "-m", "pip", "install", *([] if in_venv() else ["--user"]), *need]
        print(f"[deps] installing: {' '.join(need)}", flush=True)
        rc = subprocess.run(cmd).returncode
        if rc != 0:
            print(f"[deps] pip exited {rc}; install manually: {' '.join(shlex.quote(c) for c in cmd)}", file=sys.stderr)
        # make a just-created user site-packages importable in this process
        user_site = site.getusersitepackages()
        if os.path.isdir(user_site) and user_site not in sys.path:
            sys.path.append(user_site)
        importlib.invalidate_caches()
    for exe, how in SYSTEM_CLIS.items():
        if shutil.which(exe) is None:
            print(f"[deps] '{exe}' not found on PATH: {how}", file=sys.stderr)
    if require_textual and importlib.util.find_spec("textual") is None:
        sys.exit("[deps] textual is required for the TUI: pip install textual")


if __name__ == "__main__":
    ensure()
    left = missing()
    print("[deps] all Python dependencies present" if not left else f"[deps] still missing: {left}")
