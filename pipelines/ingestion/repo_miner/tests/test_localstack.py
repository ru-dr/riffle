"""localstack.py: the local Loki + Airflow stack that the TUI starts for a watched run. Docker is faked."""
import json
import os
import subprocess
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import localstack  # noqa: E402


def _cp(rc=0, out="", err=""):
    return subprocess.CompletedProcess([], rc, out, err)


def test_enabled_reads_the_environment(monkeypatch):
    monkeypatch.delenv("RIFFLE_LOCAL_STACK", raising=False)
    assert not localstack.enabled()
    for v in ("1", "true", "ON"):
        monkeypatch.setenv("RIFFLE_LOCAL_STACK", v)
        assert localstack.enabled()


def test_env_files_get_fresh_secrets_and_existing_ones_are_kept(tmp_path, monkeypatch):
    for d, body in (("store", "RIFFLE_PORT=8470\n"), ("logging", "GRAFANA_ADMIN_USER=admin\nGF_SECURITY_ADMIN_PASSWORD=\nLOKI_RETENTION=168h\n"),
                    ("air", "AIRFLOW_FERNET_KEY=\nAIRFLOW_API_SECRET_KEY=\nAIRFLOW_JWT_SECRET=\nPOSTGRES_PASSWORD=\n"
                            "AIRFLOW_USERS=\nAIRFLOW_PASSWORDS=\nAIRFLOW_PORT=8080\n")):
        (tmp_path / d).mkdir()
        (tmp_path / d / ".env.example").write_text(body)
    monkeypatch.setattr(localstack, "STORE_DIR", str(tmp_path / "store"))
    monkeypatch.setattr(localstack, "LOGGING_DIR", str(tmp_path / "logging"))
    monkeypatch.setattr(localstack, "AIRFLOW_DIR", str(tmp_path / "air"))
    first = localstack.ensure_env_files()
    env = localstack._read_env(str(tmp_path / "air" / ".env"))
    assert all(env[k] for k in ("AIRFLOW_FERNET_KEY", "AIRFLOW_JWT_SECRET", "POSTGRES_PASSWORD", "AIRFLOW_USERS"))
    assert env["AIRFLOW_PORT"] == "8080"
    user, _, pw = env["AIRFLOW_PASSWORDS"].partition(":")
    assert len(pw) >= 12 and "," not in pw and ":" not in pw          # what airflow's init demands
    assert first["airflow"] == f"{user} / {pw}"
    log = localstack._read_env(str(tmp_path / "logging" / ".env"))
    assert len(log["GF_SECURITY_ADMIN_PASSWORD"]) >= 12 and log["LOKI_RETENTION"] == "168h"       # the logging stack's own variable
    assert first["grafana"] == f"admin / {log['GF_SECURITY_ADMIN_PASSWORD']}"
    assert localstack.ensure_env_files() == first                       # second call changes nothing


def test_ensure_token_makes_one_and_saves_it(tmp_path, monkeypatch):
    monkeypatch.setattr(localstack, "ENV_PATH", str(tmp_path / ".env"))
    monkeypatch.delenv(localstack.TOKEN_KEY, raising=False)
    monkeypatch.setattr(localstack, "_token_works", lambda t: False)
    seen = []
    monkeypatch.setattr(localstack, "_compose", lambda d, *a, **k: (seen.append(a), None)[1] or _cp(
        out=f"{a[-1]}: rw_abc123\nShown once; the store keeps only its hash.\n"))
    msgs = []
    tok = localstack.ensure_token(msgs.append)
    assert tok == "rw_abc123" and f"{localstack.TOKEN_KEY}=rw_abc123" in (tmp_path / ".env").read_text()
    assert seen[0][:5] == ("exec", "-T", "riffle-data", "python", "manage.py")
    # a token the store still accepts is reused, not replaced
    monkeypatch.setattr(localstack, "_token_works", lambda t: True)
    seen.clear()
    assert localstack.ensure_token(msgs.append) == "rw_abc123" and not seen


def test_sync_env_is_empty_without_a_token(monkeypatch, tmp_path):
    monkeypatch.setattr(localstack, "ENV_PATH", str(tmp_path / ".env"))
    monkeypatch.delenv(localstack.TOKEN_KEY, raising=False)
    assert localstack.sync_env() == {}
    monkeypatch.setenv(localstack.TOKEN_KEY, "rw_x")
    assert localstack.sync_env() == {"RIFFLE_SYNC_URL": localstack.STORE_URL, "RIFFLE_SYNC_TOKEN": "rw_x"}


def test_trigger_dag_passes_version_and_blank_split(monkeypatch):
    calls = []
    monkeypatch.setattr(localstack, "_airflow", lambda *a, **k: (calls.append(a), _cp())[1])
    assert localstack.trigger_dag("7.0.1", say=lambda m: None)
    args = calls[0]
    assert args[:3] == ("dags", "trigger", "riffle_miner")
    assert json.loads(args[args.index("--conf") + 1]) == {"miner_version": "7.0.1", "split_id": "", "dvc_remote": ""}


def test_trigger_failure_is_a_message_not_an_exception(monkeypatch):
    monkeypatch.setattr(localstack, "_airflow", lambda *a, **k: _cp(rc=1, err="no such service"))
    msgs = []
    assert localstack.trigger_dag("7.0.1", say=msgs.append) is False and "no such service" in msgs[0]


def test_ensure_up_stops_early_without_docker(monkeypatch):
    monkeypatch.setattr(localstack, "docker_status", lambda: (False, "docker is not installed"))
    msgs = []
    assert localstack.ensure_up(msgs.append) is False and "docker is not installed" in msgs[0]


def test_a_bad_beat_setting_is_the_default_never_a_crash(monkeypatch):
    import runlog
    for bad in ("", "abc", "nan", "-5", "0", "0.2"):
        monkeypatch.setenv("RIFFLE_HEARTBEAT_S", bad)
        assert runlog._env_float("RIFFLE_HEARTBEAT_S", 120.0) == 120.0
    monkeypatch.setenv("RIFFLE_HEARTBEAT_S", "10")
    assert runlog._env_float("RIFFLE_HEARTBEAT_S", 120.0) == 10.0


def test_the_stack_wait_is_bounded_and_parsed_safely(monkeypatch):
    monkeypatch.setenv("RIFFLE_LOCAL_WAIT_S", "soon")
    assert localstack.start_wait_s() == 120.0
    monkeypatch.setenv("RIFFLE_LOCAL_WAIT_S", "30")
    assert localstack.start_wait_s() == 30.0


# ---- from the review: never overwrite, never mint needlessly, never raise, never two starts ----------------------
def test_an_existing_env_keeps_every_key_it_has(tmp_path):
    example = tmp_path / ".env.example"
    example.write_text("RIFFLE_BUCKET=\nRIFFLE_PORT=8470\nGRAFANA_ADMIN_PASSWORD=\n# a comment\n")
    env = tmp_path / ".env"
    env.write_text("RIFFLE_BUCKET=my-bucket\nRIFFLE_PORT=9999\nEXTRA=1\nGRAFANA_ADMIN_PASSWORD=\n")
    localstack._write_env(str(env), str(example), {"GRAFANA_ADMIN_PASSWORD": "newpass"})
    got = localstack._read_env(str(env))
    assert got == {"RIFFLE_BUCKET": "my-bucket", "RIFFLE_PORT": "9999", "EXTRA": "1", "GRAFANA_ADMIN_PASSWORD": "newpass"}
    assert not [f for f in os.listdir(tmp_path) if f.endswith(".tmp")]            # replaced whole, nothing left behind
    localstack._write_env(str(env), str(example), {"GRAFANA_ADMIN_PASSWORD": "other"})
    assert localstack._read_env(str(env))["GRAFANA_ADMIN_PASSWORD"] == "newpass"   # a password that exists is kept


def test_saving_the_token_never_loses_the_rest_of_the_miners_env(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("GITHUB_TOKEN=ghp_keepme\nRIFFLE_SYNC_URL=https://team.example\n")
    monkeypatch.setattr(localstack, "ENV_PATH", str(env))
    localstack._save_token("rw_new")
    assert localstack._read_env(str(env)) == {"GITHUB_TOKEN": "ghp_keepme", "RIFFLE_SYNC_URL": "https://team.example",
                                              localstack.TOKEN_KEY: "rw_new"}


def test_a_store_that_is_only_restarting_does_not_cost_a_new_token(tmp_path, monkeypatch):
    monkeypatch.setattr(localstack, "ENV_PATH", str(tmp_path / ".env"))
    monkeypatch.setenv(localstack.TOKEN_KEY, "rw_have")
    monkeypatch.setattr(localstack, "_token_works", lambda t: None)               # no answer
    made = []
    monkeypatch.setattr(localstack, "_compose", lambda *a, **k: made.append(a) or _cp())
    assert localstack.ensure_token(lambda m: None) == "rw_have" and not made
    monkeypatch.setattr(localstack, "_token_works", lambda t: False)              # answered, and refused it
    monkeypatch.setattr(localstack, "_compose", lambda d, *a, **k: _cp(out=f"{a[-1]}: rw_fresh\n"))
    assert localstack.ensure_token(lambda m: None) == "rw_fresh"


def test_docker_timeouts_and_a_missing_docker_are_results_not_exceptions(monkeypatch):
    def slow(*a, **k):
        raise subprocess.TimeoutExpired("docker", 1)
    monkeypatch.setattr(subprocess, "run", slow)
    assert localstack._compose(".", "up", timeout=1).returncode == 124
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("docker")))
    assert localstack._compose(".", "up").returncode == 127


def test_ensure_up_never_raises_and_never_runs_twice_at_once(monkeypatch):
    msgs = []
    monkeypatch.setattr(localstack, "_ensure_up", lambda say: (_ for _ in ()).throw(RuntimeError("boom")))
    assert localstack.ensure_up(msgs.append) is False and "boom" in msgs[0]
    started, release = threading.Event(), threading.Event()
    monkeypatch.setattr(localstack, "_ensure_up", lambda say: (started.set(), release.wait(5), True)[2])
    t = threading.Thread(target=localstack.ensure_up, args=(lambda m: None,))
    t.start()
    assert started.wait(5)
    msgs.clear()
    assert localstack.ensure_up(msgs.append) is False and "already starting" in msgs[0]
    release.set()
    t.join(5)


def test_env_numbers_that_a_timer_cannot_take_fall_back(monkeypatch):
    import runlog
    for bad in ("inf", "1e30", "-inf", "nan"):
        monkeypatch.setenv("RIFFLE_HEARTBEAT_S", bad)
        assert runlog._env_float("RIFFLE_HEARTBEAT_S", 120.0) == 120.0


def test_a_miner_started_in_the_background_still_honours_stop():
    """`cmd &` in a script leaves SIGINT ignored; the miner puts its handler back so Stop and ctrl+c work."""
    import signal
    code = ("import signal, runlog;"
            "print(signal.getsignal(signal.SIGINT) == signal.SIG_IGN);"
            "runlog.honour_sigint();"
            "print(signal.getsignal(signal.SIGINT) == signal.default_int_handler)")
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    r = subprocess.run([sys.executable, "-c", code], cwd=here, capture_output=True, text=True, timeout=30,
                       preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_IGN))
    assert r.stdout.split() == ["True", "True"], r.stderr
    r = subprocess.run([sys.executable, "-c", "import signal, runlog; before = signal.getsignal(signal.SIGINT); runlog.honour_sigint(); print(before == signal.getsignal(signal.SIGINT))"],
                       cwd=here, capture_output=True, text=True, timeout=30)
    assert r.stdout.split() == ["True"]                          # a normally started miner is left alone
