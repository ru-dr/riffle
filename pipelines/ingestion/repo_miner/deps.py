"""
deps.py — install the miner's Python dependencies on first run.

tui.py calls ensure() before importing textual, so `python tui.py` on a fresh
machine installs what it needs. Python packages are pip-installed (with
--user outside a virtualenv); tools pip can't provide (git, claude) are only
checked and reported. Set RIFFLE_NO_INSTALL=1 to skip installing.
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import shutil
import site
import subprocess
import sys

# import name -> pip spec (kept in step with requirements.txt)
PY_PACKAGES = {
    "textual": "textual>=8.0",
    "pandas": "pandas>=2.0",
    "pyarrow": "pyarrow>=14.0",
    "lizard": "lizard>=1.17",
}
# CLI tools pip can install -> pip spec
PIP_CLIS = {"semgrep": "semgrep>=1.0"}
# CLI tools the user must install themselves -> how
SYSTEM_CLIS = {
    "git": "install git with your package manager",
    "claude": "install Claude Code, then run `claude` once and /login (needed for --llm)",
}


def missing() -> list[str]:
    specs = [spec for mod, spec in PY_PACKAGES.items()
             if importlib.util.find_spec(mod) is None]
    specs += [spec for exe, spec in PIP_CLIS.items() if shutil.which(exe) is None]
    return specs


def ensure() -> None:
    need = missing()
    if need and not os.getenv("RIFFLE_NO_INSTALL"):
        in_venv = sys.prefix != sys.base_prefix
        cmd = [sys.executable, "-m", "pip", "install", *([] if in_venv else ["--user"]), *need]
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
    if importlib.util.find_spec("textual") is None:
        sys.exit("[deps] textual is required for the TUI: pip install textual")


if __name__ == "__main__":
    ensure()
    left = missing()
    print("[deps] all Python dependencies present" if not left else f"[deps] still missing: {left}")
