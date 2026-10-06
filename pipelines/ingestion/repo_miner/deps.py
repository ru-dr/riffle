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
import shutil
import site
import subprocess
import sys
import sysconfig

HERE = os.path.dirname(os.path.abspath(__file__))
VENV_DIR = os.path.join(HERE, ".venv")

# import name -> pip spec (kept in step with requirements.txt)
PY_PACKAGES = {
    "textual": "textual>=8.0",
    "pandas": "pandas>=2.0",
    "pyarrow": "pyarrow>=14.0",
    "lizard": "lizard>=1.17",
    "psutil": "psutil>=5.9",            # TUI RAM/CPU readout
}
# CLI tools pip can install -> pip spec
PIP_CLIS = {"semgrep": "semgrep>=1.0"}
# CLI tools the user must install themselves -> how
SYSTEM_CLIS = {
    "git": "install git with your package manager",
    "claude": "install Claude Code, then run `claude` once and /login (needed for --llm)",
}


def _all_specs() -> list[str]:
    """Everything, for a fresh venv (it starts empty, so install the lot)."""
    return [*PY_PACKAGES.values(), *PIP_CLIS.values()]


def missing() -> list[str]:
    specs = [spec for mod, spec in PY_PACKAGES.items()
             if importlib.util.find_spec(mod) is None]
    specs += [spec for exe, spec in PIP_CLIS.items() if shutil.which(exe) is None]
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
    """Create repo_miner/.venv. Returns (ok, error text). On Debian/Ubuntu this
    fails without the python3-venv package (no ensurepip)."""
    proc = subprocess.run([sys.executable, "-m", "venv", VENV_DIR], capture_output=True, text=True)
    if proc.returncode == 0 and os.path.isfile(venv_python()):
        return True, ""
    shutil.rmtree(VENV_DIR, ignore_errors=True)       # a half-made venv would be re-entered
    return False, (proc.stderr or proc.stdout).strip()


def venv_package_hint() -> str:
    v = f"{sys.version_info.major}.{sys.version_info.minor}"
    return f"sudo apt install python3-venv  (or python{v}-venv)"


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
            print(f"[deps] pip exited {rc}; install manually: {' '.join(cmd)}", file=sys.stderr)
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
