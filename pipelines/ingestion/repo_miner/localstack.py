"""
localstack.py — start the run-tracking stack on this PC, for a miner run you want to watch (ADR 0017).

Three Docker Compose projects already in the repo make it:
  infra/docker/riffle-data   the store (port 8470) plus, with compose.logs.yaml, Loki and Grafana (3000)
  infra/docker/airflow       Airflow (8080) and its `riffle_miner` DAG, one task per miner stage

ensure_up() creates their .env files with fresh secrets when missing, starts both, makes a write-only
token for this PC in the local store and keeps it in the miner's .env as RIFFLE_LOCAL_SYNC_TOKEN. The TUI
then gives the miner RIFFLE_SYNC_URL / RIFFLE_SYNC_TOKEN for that run only (sync_env()), so its logs and tables
land in the local store, the store forwards each trace line to Loki, and trigger_dag() refreshes the Airflow graph. A PC that is
joined to a team store keeps reporting there, and the local store gets a copy (RIFFLE_MIRROR_*, sync.py). The miner's
own .env is never rewritten to point at the local store.

Standard library only; every function returns instead of raising, and says why through `say`.
"""
from __future__ import annotations

import base64
import json
import os
import platform
import re
import secrets
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
STORE_DIR = os.path.join(ROOT, "infra", "docker", "riffle-data")
AIRFLOW_DIR = os.path.join(ROOT, "infra", "docker", "airflow")
ENV_PATH = os.path.join(HERE, ".env")

STORE_URL = "http://127.0.0.1:8470"
GRAFANA_URL = "http://127.0.0.1:3000"
AIRFLOW_URL = "http://127.0.0.1:8080"
DAG_ID = "riffle_miner"
TOKEN_KEY = "RIFFLE_LOCAL_SYNC_TOKEN"

_HEALTH = {"store": STORE_URL + "/healthz", "grafana": GRAFANA_URL + "/api/health",
           "airflow": AIRFLOW_URL + "/api/v2/monitor/health"}


def start_wait_s() -> float:
    """How long a run waits for the stack before it starts without it (RIFFLE_LOCAL_WAIT_S, default 120)."""
    try:
        v = float(os.environ.get("RIFFLE_LOCAL_WAIT_S") or 120)
    except ValueError:
        return 120.0
    return v if v > 0 else 120.0


def enabled() -> bool:
    return os.environ.get("RIFFLE_LOCAL_STACK", "").strip().lower() in ("1", "true", "yes", "on")


def docker_status() -> tuple[bool, str]:
    """(usable, why not): the docker CLI, the compose plugin and a reachable daemon."""
    if shutil.which("docker") is None:
        return False, "docker is not installed"
    try:
        r = subprocess.run(["docker", "compose", "version"], capture_output=True, text=True, timeout=10)
        if r.returncode:
            return False, "the `docker compose` plugin is missing"
        r = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"docker did not answer ({type(e).__name__})"
    return (True, "") if r.returncode == 0 else (False, "the docker daemon is not running (or you lack permission)")


def _get_ok(url: str, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return 200 <= r.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


def status() -> dict[str, bool]:
    """Which parts answer right now."""
    return {name: _get_ok(url) for name, url in _HEALTH.items()}


# ------------------------------------------------------------------ .env files
def _read_env(path: str) -> dict[str, str]:
    out = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def _write_env(path: str, example: str, values: dict[str, str]) -> None:
    """Make `path` from `example`: every key already in `path` keeps its value (a store's bucket, domain and port are
    never reset), `values` fill the keys that are blank, and keys only `path` has are kept at the end. The file is
    replaced whole, so a crash leaves the old one."""
    with open(example, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    have = _read_env(path)
    out, seen = [], set()
    for line in lines:
        k = line.split("=", 1)[0].strip()
        if "=" in line and not line.lstrip().startswith("#"):
            seen.add(k)
            out.append(f"{k}={have.get(k) or values.get(k) or ''}" if (k in have or k in values) else line)
        else:
            out.append(line)
    out += [f"{k}={v}" for k, v in have.items() if k not in seen]
    _atomic_write(path, "\n".join(out) + "\n")


def _atomic_write(path: str, text: str) -> None:
    tmp = f"{path}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def _word() -> str:
    return secrets.token_urlsafe(12).replace("_", "x").replace("-", "y")   # airflow logins reject , and :


def ensure_env_files() -> dict[str, str]:
    """Make both Compose .env files if missing. Returns the logins to show: grafana and airflow."""
    s_env, a_env = os.path.join(STORE_DIR, ".env"), os.path.join(AIRFLOW_DIR, ".env")
    if not os.path.isfile(s_env):
        _write_env(s_env, s_env + ".example", {"GRAFANA_ADMIN_PASSWORD": _word()})
    elif not _read_env(s_env).get("GRAFANA_ADMIN_PASSWORD"):
        _write_env(s_env, s_env + ".example", {"GRAFANA_ADMIN_PASSWORD": _word()})
    if not os.path.isfile(a_env):
        _write_env(a_env, a_env + ".example", {
            "AIRFLOW_FERNET_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode(),
            "AIRFLOW_API_SECRET_KEY": secrets.token_urlsafe(32),
            "AIRFLOW_JWT_SECRET": secrets.token_urlsafe(32),
            "POSTGRES_PASSWORD": secrets.token_urlsafe(24),
            "AIRFLOW_USERS": "riffle:admin",
            "AIRFLOW_PASSWORDS": "riffle:" + _word(),
        })
    s, a = _read_env(s_env), _read_env(a_env)
    user, _, pw = (a.get("AIRFLOW_PASSWORDS") or "").split(",")[0].partition(":")
    return {"grafana": f"admin / {s.get('GRAFANA_ADMIN_PASSWORD', '?')}", "airflow": f"{user} / {pw}"}


# ------------------------------------------------------------------ docker steps
def _compose(directory: str, *args: str, timeout: int = 900) -> subprocess.CompletedProcess:
    """`docker compose ...`; a timeout or a missing docker is a failed result (rc 124 or 127), never an exception."""
    try:
        return subprocess.run(["docker", "compose", *args], cwd=directory, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 124, "", f"timed out after {timeout}s")
    except OSError as e:
        return subprocess.CompletedProcess(args, 127, "", f"{type(e).__name__}: {e}")


def _wait(name: str, seconds: int, say) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if status()[name]:
            return True
        time.sleep(3)
    say(f"[local] {name} did not answer at {_HEALTH[name]} within {seconds}s")
    return False


def _save_token(token: str) -> None:
    lines = []
    try:
        with open(ENV_PATH, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        pass
    lines = [ln for ln in lines if ln.partition("=")[0].strip() != TOKEN_KEY] + [f"{TOKEN_KEY}={token}"]
    _atomic_write(ENV_PATH, "\n".join(lines) + "\n")       # the miner's real .env: replaced whole, never truncated first
    os.environ[TOKEN_KEY] = token


def _token_works(token: str) -> bool | None:
    """True: the store knows this token. False: it answered and refused it. None: it did not answer (restarting)."""
    req = urllib.request.Request(STORE_URL + "/v1/whoami", headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status == 200
    except urllib.error.HTTPError as e:
        return None if e.code >= 500 else False
    except (urllib.error.URLError, OSError, ValueError):
        return None


def local_token() -> str:
    return os.environ.get(TOKEN_KEY) or _read_env(ENV_PATH).get(TOKEN_KEY, "")


def ensure_token(say) -> str:
    """The write token for this PC in the local store: the saved one when the store still accepts it, else a new one."""
    tok = local_token()
    works = _token_works(tok) if tok else False
    if tok and works is not False:
        return tok                                          # known, or the store just did not answer: keep it, never mint another
    name = "local-" + re.sub(r"[^a-z0-9]+", "", platform.node().lower())[:12] + time.strftime("%m%d%H%M")
    r = _compose(STORE_DIR, "exec", "-T", "riffle-data", "python", "manage.py", "add-pc", name, timeout=60)
    m = re.search(rf"{re.escape(name)}:\s*(\S+)", r.stdout or "")
    if not m:
        say(f"[local] could not make a local PC token: {(r.stderr or r.stdout).strip()[-200:]}")
        return ""
    _save_token(m.group(1))
    say(f"[local] made a write token for this PC ({name}), kept as {TOKEN_KEY} in .env")
    return m.group(1)


_STARTING = threading.Lock()


def ensure_up(say=print) -> bool:
    """Start the store, Loki/Grafana and Airflow if they are not answering, and make the PC token.
    Slow the first time (images are built). True when the miner can send to the local store. One start at a time:
    a second call while one runs returns False at once. Never raises."""
    if not _STARTING.acquire(blocking=False):
        say("[local] the stack is already starting; this run goes on without waiting for it")
        return False
    try:
        return _ensure_up(say)
    except Exception as e:                                  # noqa: BLE001  (tracking is optional)
        say(f"[local] stack not started: {type(e).__name__}: {e}")
        return False
    finally:
        _STARTING.release()


def _ensure_up(say) -> bool:
    ok, why = docker_status()
    if not ok:
        say(f"[local] stack not started: {why}")
        return False
    logins = ensure_env_files()
    st = status()
    if not (st["store"] and st["grafana"]):
        say("[local] starting the store, Loki and Grafana (first run builds images)...")
        r = _compose(STORE_DIR, "-f", "compose.yaml", "-f", "compose.logs.yaml", "up", "-d", "--build")
        if r.returncode:
            say(f"[local] store stack failed: {(r.stderr or r.stdout).strip()[-300:]}")
            return False
    if not _wait("store", 90, say):
        return False
    if not st["airflow"]:
        say("[local] starting Airflow (first run builds the image, a few minutes)...")
        r = _compose(AIRFLOW_DIR, "up", "-d", "--build")
        if r.returncode:
            say(f"[local] airflow failed: {(r.stderr or r.stdout).strip()[-300:]}")
            say("[local] the miner still runs; logs reach Loki and Grafana without it")
        else:
            _wait("airflow", 240, say)
    if status()["airflow"]:
        _unpause_dag(say)                                   # every start, not only the one that built it: a DAG left paused never runs
    token = ensure_token(say)
    say(f"[local] Grafana {GRAFANA_URL} ({logins['grafana']}) · Airflow {AIRFLOW_URL} ({logins['airflow']}) "
        f"· store {STORE_URL}")
    return bool(token)


def _unpause_dag(say) -> None:
    """Unpause riffle_miner, retrying while Airflow is still reading the DAG file."""
    for _ in range(6):
        r = _airflow("dags", "unpause", DAG_ID)
        if r.returncode == 0:
            return
        time.sleep(5)
    say(f"[local] could not unpause {DAG_ID}; unpause it in the Airflow page")


def sync_env() -> dict[str, str]:
    """Environment for the miner child process so its output reaches the local store. Empty if there is no token.
    A PC that is joined to a team store keeps reporting to it, and the local store gets a copy (the mirror), so
    watching a run on this PC never hides the PC from the team; a PC that is not joined reports to the local store."""
    tok = local_token()
    if not tok:
        return {}
    if os.environ.get("RIFFLE_SYNC_URL") and os.environ.get("RIFFLE_SYNC_TOKEN"):
        return {"RIFFLE_MIRROR_URL": STORE_URL, "RIFFLE_MIRROR_TOKEN": tok}
    return {"RIFFLE_SYNC_URL": STORE_URL, "RIFFLE_SYNC_TOKEN": tok}


def live_env() -> dict[str, str]:
    """Faster beats for a watched run (the team store's defaults are 3 and 15 minutes, too slow to watch): the
    miner uploads its trace and log every 10 s, and writes a heartbeat of what each worker is doing every 10 s."""
    return {"RIFFLE_SYNC_BEAT_S": "10", "RIFFLE_SYNC_LOG_BEAT_S": "10", "RIFFLE_HEARTBEAT_S": "10"}


# ------------------------------------------------------------------ Airflow
def _airflow(*args: str, timeout: int = 90) -> subprocess.CompletedProcess:
    return _compose(AIRFLOW_DIR, "exec", "-T", "airflow-scheduler", "airflow", *args, timeout=timeout)


def trigger_dag(version: str, split_id: str = "", dvc_remote: str = "", say=print) -> bool:
    """Start a riffle_miner run, which reads the traces now in the store and draws each stage. A blank split_id
    means every split the store has for that version. A failure is a message, never an exception."""
    conf = json.dumps({"miner_version": version, "split_id": split_id, "dvc_remote": dvc_remote})
    try:
        r = _airflow("dags", "trigger", DAG_ID, "--run-id", "tui-" + time.strftime("%Y%m%d-%H%M%S"), "--conf", conf)
    except (OSError, subprocess.TimeoutExpired) as e:
        say(f"[local] could not trigger the DAG: {type(e).__name__}")
        return False
    if r.returncode:
        say(f"[local] could not trigger the DAG: {(r.stderr or r.stdout).strip()[-200:]}")
    return r.returncode == 0
