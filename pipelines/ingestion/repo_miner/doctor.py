"""
doctor.py — check (and help fix) the miner's environment.

tui.py runs this on every launch, before the UI opens. It checks Python, git,
the Python packages, semgrep, the GitHub token, the LLM setup, optional API-diff
tools, and disk/RAM, then offers to fix what it can:

  - missing Python packages / semgrep: pip-installs them (one prompt)
  - GITHUB_TOKEN missing, a template placeholder, or rejected by GitHub:
    asks for a token (hidden input), checks it, and saves it to .env (mode 600)
  - git / claude missing: shows the install command for this OS and runs it
    only if you say yes

    python doctor.py            # interactive check + fixes
    python doctor.py --check    # report only, never prompts (exit 1 on problems)

Non-interactive (no TTY) runs only report. RIFFLE_NO_DOCTOR=1 skips the doctor
in tui.py; RIFFLE_NO_INSTALL=1 makes it report without installing anything.
"""
from __future__ import annotations

import getpass
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass

import deps

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(HERE, ".env")
ENV_EXAMPLE = os.path.join(HERE, ".env.example")
MIN_PY = (3, 10)
# values .env.example ships with; a copied template must not count as "set"
PLACEHOLDERS = {"", "TOKEN", "KEY", "ghp_xxxxxxxxxxxx", "changeme", "your_token_here"}
TOKEN_URL = "https://github.com/settings/tokens/new?scopes=public_repo&description=riffle-miner"
# optional API-contract tools (only used with "API contract diff" on)
OPTIONAL_TOOLS = {
    "oasdiff": "https://github.com/oasdiff/oasdiff#installation",
    "buf": "https://buf.build/docs/installation",
    "graphql-inspector": "npm i -g @graphql-inspector/cli",
}

OK, WARN, FAIL = "ok", "warn", "fail"
_C = {OK: "\033[32m✓\033[0m", WARN: "\033[33m!\033[0m", FAIL: "\033[31m✗\033[0m"}


@dataclass
class Result:
    name: str
    status: str
    detail: str
    fix: str | None = None        # key of the fixer to offer, if any


# ------------------------------------------------------------------- helpers
def _sym(status: str) -> str:
    return _C[status] if sys.stdout.isatty() else {OK: "OK  ", WARN: "WARN", FAIL: "FAIL"}[status]


def _ask(prompt: str, default: bool = True) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    try:
        ans = input(f"  {prompt} {hint} ").strip().lower()
    except EOFError:
        return default
    return default if not ans else ans in ("y", "yes")


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


def _install_cmd(tool: str) -> list[str] | None:
    """A command that installs `tool` on this OS, or None if we don't know one."""
    system = platform.system()
    if tool == "claude":
        if shutil.which("npm"):
            return ["npm", "install", "-g", "@anthropic-ai/claude-code"]
        return None
    if tool == "git":
        if system == "Darwin" and shutil.which("brew"):
            return ["brew", "install", "git"]
        if system == "Windows" and shutil.which("winget"):
            return ["winget", "install", "--id", "Git.Git", "-e"]
        for pm, cmd in (("dnf", ["sudo", "dnf", "install", "-y", "git"]),
                        ("apt-get", ["sudo", "apt-get", "install", "-y", "git"]),
                        ("pacman", ["sudo", "pacman", "-S", "--noconfirm", "git"]),
                        ("zypper", ["sudo", "zypper", "install", "-y", "git"])):
            if shutil.which(pm):
                return cmd
    return None


# -------------------------------------------------------------------- checks
def run_checks() -> list[Result]:
    res: list[Result] = []
    py = sys.version_info
    res.append(Result("Python", OK if py >= MIN_PY else FAIL,
                      f"{py.major}.{py.minor}.{py.micro}"
                      + ("" if py >= MIN_PY else f" (need {MIN_PY[0]}.{MIN_PY[1]}+)")))

    # Where packages go. Ubuntu/Debian 23.04+ (PEP 668) block pip outside a
    # venv, so there they go into repo_miner/.venv, which needs python3-venv.
    if deps.in_venv():
        where = "project .venv" if os.path.abspath(sys.prefix) == os.path.abspath(deps.VENV_DIR) \
            else f"virtualenv {sys.prefix}"
        res.append(Result("Environment", OK, where))
    elif deps.externally_managed():
        if importlib.util.find_spec("ensurepip") is None:
            res.append(Result("Environment", FAIL,
                              "system Python is externally managed (PEP 668) and the venv "
                              f"module is missing: {deps.venv_package_hint()}", fix="venv"))
        else:
            res.append(Result("Environment", OK,
                              "system Python is externally managed; packages go in .venv"))
    else:
        res.append(Result("Environment", OK, "system Python (pip --user)"))

    git = shutil.which("git")
    if git:
        ver = subprocess.run(["git", "--version"], capture_output=True, text=True).stdout.strip()
        res.append(Result("git", OK, ver.replace("git version ", "")))
    else:
        res.append(Result("git", FAIL, "not found on PATH (the miner needs it)", fix="git"))

    need = deps.missing()
    res.append(Result("Python packages", OK if not need else FAIL,
                      "all installed" if not need else "missing: " + ", ".join(need),
                      fix="pip" if need else None))

    tok = _env("GITHUB_TOKEN")
    st, detail = _github_token_status(tok)
    res.append(Result("GITHUB_TOKEN", st, detail, fix="token" if st == FAIL else None))

    backend = _env("LLM_BACKEND") or "claude_cli"
    if backend == "claude_cli":
        if shutil.which("claude"):
            ver = subprocess.run(["claude", "--version"], capture_output=True,
                                 text=True).stdout.strip()
            res.append(Result("LLM (claude CLI)", OK,
                              f"{ver or 'found'} · use Test LLM (ctrl+t) to check login"))
        else:
            res.append(Result("LLM (claude CLI)", WARN,
                              "`claude` not found: the LLM flags option won't work", fix="claude"))
    else:
        key = _env("LLM_API_KEY")
        has_sdk = importlib.util.find_spec("openai") is not None
        bad = [m for m, cond in (("LLM_API_KEY not set", key in PLACEHOLDERS),
                                 ("openai package missing", not has_sdk)) if cond]
        res.append(Result(f"LLM ({backend})", WARN if bad else OK,
                          "; ".join(bad) or "LLM_API_KEY set, openai installed",
                          fix="llm_key" if key in PLACEHOLDERS else
                          ("openai" if not has_sdk else None)))

    missing_opt = [t for t in OPTIONAL_TOOLS if shutil.which(t) is None]
    res.append(Result("API-diff tools (optional)", OK if not missing_opt else WARN,
                      "all installed" if not missing_opt else
                      "missing: " + ", ".join(missing_opt) + " (only for API contract diff)"))

    free = shutil.disk_usage(HERE).free
    res.append(Result("Disk free", OK if free >= 20 * 2**30 else WARN,
                      f"{free / 2**30:.0f} GB" + ("" if free >= 20 * 2**30 else
                                                  " (big repos need 5-20 GB each to clone)")))
    try:
        import psutil
        ram = psutil.virtual_memory().total
        res.append(Result("RAM", OK if ram >= 8 * 2**30 else WARN,
                          f"{ram / 2**30:.0f} GB" + ("" if ram >= 8 * 2**30 else
                                                     " (lower PR workers on large repos)")))
    except ImportError:
        pass
    return res


# --------------------------------------------------------------------- fixes
def _fix_pip() -> None:
    if os.getenv("RIFFLE_NO_INSTALL"):
        print("  RIFFLE_NO_INSTALL is set; skipping install.")
        return
    deps.ensure(require_textual=False)


def _fix_token() -> None:
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
        print(f"  {_sym(st)} {detail} · saved to {ENV_PATH} (private, gitignored)")
        return


def _fix_llm_key() -> None:
    try:
        key = getpass.getpass("  Paste LLM_API_KEY (hidden, Enter to skip): ").strip()
    except EOFError:
        return
    if key:
        _set_env_value("LLM_API_KEY", key)
        print(f"  saved to {ENV_PATH}")


def _fix_openai() -> None:
    if not deps.in_venv() and deps.externally_managed():
        if not os.path.isfile(deps.venv_python()):
            ok, err = deps.create_venv()
            if not ok:
                print(f"  could not create .venv: {err[-200:]}\n  {deps.venv_package_hint()}")
                return
        subprocess.run([deps.venv_python(), "-m", "pip", "install", "openai>=1.0"])
        return
    subprocess.run([sys.executable, "-m", "pip", "install",
                    *([] if deps.in_venv() else ["--user"]), "openai>=1.0"])


def _fix_venv() -> None:
    v = f"python{sys.version_info.major}.{sys.version_info.minor}-venv"
    cmd = (["sudo", "apt-get", "install", "-y", v] if shutil.which("apt-get") else None)
    if cmd is None:
        print(f"  Install your distro's venv package: {deps.venv_package_hint()}")
        return
    if _ask(f"Run `{' '.join(cmd)}`?", default=True):
        if subprocess.run(cmd).returncode != 0:            # older releases name it python3-venv
            subprocess.run(["sudo", "apt-get", "install", "-y", "python3-venv"])
    else:
        print(f"  Skipped. To install later: {' '.join(cmd)}")


def _fix_system(tool: str) -> None:
    cmd = _install_cmd(tool)
    hint = deps.SYSTEM_CLIS.get(tool, "")
    if cmd is None:
        print(f"  No automatic installer found here. {hint}")
        return
    if _ask(f"Run `{' '.join(cmd)}`?", default=False):
        subprocess.run(cmd)
    else:
        print(f"  Skipped. To install later: {' '.join(cmd)}")


FIXERS = {
    "venv": ("Install the Python venv module now?", _fix_venv),
    "pip": ("Install the missing Python packages now?", _fix_pip),
    "token": ("Set up a GitHub token now?", _fix_token),
    "llm_key": ("Set LLM_API_KEY now?", _fix_llm_key),
    "openai": ("Install the openai package now?", _fix_openai),
    "git": ("Install git now?", lambda: _fix_system("git")),
    "claude": ("Install Claude Code (for LLM flags) now?", lambda: _fix_system("claude")),
}


# ---------------------------------------------------------------------- main
def _report(results: list[Result]) -> None:
    w = max(len(r.name) for r in results)
    for r in results:
        print(f"  {_sym(r.status)} {r.name.ljust(w)}  {r.detail}")


def run(interactive: bool | None = None) -> tuple[bool, list[Result]]:
    """Check, offer fixes, re-check. Returns (usable, final results); usable is
    False if a required check still fails (tui.py then exits instead of opening
    a broken UI)."""
    if interactive is None:
        interactive = sys.stdin.isatty() and sys.stdout.isatty()
    print("Riffle doctor — checking your environment…")
    results = run_checks()
    _report(results)
    todo = [r for r in results if r.fix and r.status in (FAIL, WARN)]
    if todo and interactive:
        print()
        for r in todo:
            question, fixer = FIXERS[r.fix]
            if _ask(f"{r.name}: {question}", default=r.status == FAIL):
                fixer()
        print("\nRe-checking…")
        results = run_checks()
        _report(results)
    failed = [r for r in results if r.status == FAIL]
    if failed:
        print("\n" + ", ".join(r.name for r in failed)
              + (" still needs" if len(failed) == 1 else " still need") + " attention"
              + ("" if interactive else " (run `python doctor.py` to fix interactively)"))
    elif any(r.status == WARN for r in results):
        print("\nReady (warnings above are optional features).")
    else:
        print("\nAll good.")
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
