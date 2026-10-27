"""The logging stack's configuration: Loki, Alloy, Grafana and the Compose file that runs them.

No container is started here. These tests keep the properties that matter from drifting:
exact image versions, ports private to this machine, nothing sent to Grafana Labs, the
three parts agreeing on addresses and paths, and a bounded retention. Whether the stack
actually works is shown by starting it; see infra/README.md.

Run: python -m pytest infra/tests/test_logging_stack.py
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

INFRA = Path(__file__).resolve().parents[1]
REPO = INFRA.parent
COMPOSE = INFRA / "compose.logging.yaml"
LOKI = INFRA / "loki" / "config.yaml"
ALLOY = INFRA / "alloy" / "config.alloy"
DATASOURCE = INFRA / "grafana" / "provisioning" / "datasources" / "loki.yaml"
ENV_EXAMPLE = INFRA / ".env.example"


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def loki() -> dict:
    return yaml.safe_load(LOKI.read_text(encoding="utf-8"))


def alloy_text() -> str:
    """The Alloy file with runs of spaces and tabs collapsed: `alloy fmt` owns the alignment."""
    return re.sub(r"[ 	]+", " ", ALLOY.read_text(encoding="utf-8"))


def env_example_names() -> set[str]:
    """Every variable the example file documents, whether set or commented out."""
    names = set()
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        match = re.match(r"#?\s*([A-Z][A-Z0-9_]+)=", line)
        if match:
            names.add(match.group(1))
    return names


def env_example_value(name: str) -> str | None:
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip()
    return None


def test_the_stack_is_loki_alloy_and_grafana(compose):
    assert compose["name"] == "riffle-logging"
    assert set(compose["services"]) == {"loki", "alloy", "grafana"}


def test_every_image_is_pinned_to_an_exact_version(compose):
    for name, service in compose["services"].items():
        tag = service["image"].rsplit(":", 1)[-1]
        assert re.fullmatch(r"v?\d+\.\d+\.\d+", tag), f"{name}: {service['image']} is not an exact version"
        assert "latest" not in service["image"]


def test_every_published_port_is_private_unless_you_change_it(compose):
    for name, service in compose["services"].items():
        for port in service.get("ports", []):
            assert str(port).startswith("${LOGGING_BIND_ADDR:-127.0.0.1}:"), f"{name}: {port}"
    assert env_example_value("LOGGING_BIND_ADDR") == "127.0.0.1"


def test_nothing_is_privileged_or_shares_the_host(compose):
    for name, service in compose["services"].items():
        for risky in ("privileged", "network_mode", "pid", "ipc", "cap_add", "devices"):
            assert risky not in service, f"{name} sets {risky}"


def test_only_alloy_mounts_the_docker_socket_and_only_read_only(compose):
    for name, service in compose["services"].items():
        sockets = [v for v in service.get("volumes", []) if "docker.sock" in str(v)]
        if name == "alloy":
            assert sockets == ["/var/run/docker.sock:/var/run/docker.sock:ro"]
        else:
            assert sockets == [], f"{name} mounts the Docker socket"


def test_the_grafana_password_is_passed_through_never_written_and_never_in_a_message(compose):
    """Passed through from .env, with no value in the file. Putting an error message after a
    question mark on this line made GitGuardian report a hardcoded password, because a scanner
    reads the message as the value. The guard in the entrypoint is what enforces it instead."""
    environment = compose["services"]["grafana"]["environment"]
    assert "GF_SECURITY_ADMIN_PASSWORD" in environment
    assert environment["GF_SECURITY_ADMIN_PASSWORD"] is None, "a key with no value is passed through from .env"
    assert env_example_value("GF_SECURITY_ADMIN_PASSWORD") == "", "the example must not hold a password"
    for number, line in enumerate(COMPOSE.read_text(encoding="utf-8").splitlines(), 1):
        if re.search("password|secret|token", line, re.IGNORECASE) and ":?" in line:
            pytest.fail(f"compose.logging.yaml:{number} puts a message after :? on a secret-looking line")


def test_nothing_is_sent_to_grafana_labs(compose, loki):
    env = compose["services"]["grafana"]["environment"]
    for key in ("GF_ANALYTICS_REPORTING_ENABLED", "GF_ANALYTICS_CHECK_FOR_UPDATES", "GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES"):
        assert env[key] == "false", key
    assert "--disable-reporting" in compose["services"]["alloy"]["command"]
    assert loki["analytics"]["reporting_enabled"] is False


def test_grafana_has_no_sign_up_and_no_anonymous_access(compose):
    env = compose["services"]["grafana"]["environment"]
    assert env["GF_USERS_ALLOW_SIGN_UP"] == "false" and env["GF_AUTH_ANONYMOUS_ENABLED"] == "false"


def test_every_mounted_config_file_exists(compose):
    for name, service in compose["services"].items():
        for volume in service.get("volumes", []):
            source = str(volume).split(":", 1)[0]
            if source.startswith("./"):
                assert (INFRA / source).exists(), f"{name} mounts {source}, which does not exist"


def test_loki_deletes_old_logs_and_the_retention_comes_from_one_variable(compose, loki):
    assert loki["compactor"]["retention_enabled"] is True
    assert loki["compactor"]["delete_request_store"], "required for retention in Loki 3"
    assert loki["limits_config"]["retention_period"] == "${LOKI_RETENTION:-168h}"
    assert compose["services"]["loki"]["environment"]["LOKI_RETENTION"] == "${LOKI_RETENTION:-168h}"
    assert env_example_value("LOKI_RETENTION") == "168h"


def test_loki_uses_the_current_index_schema(loki):
    config = loki["schema_config"]["configs"][-1]
    assert (config["store"], config["schema"], config["object_store"]) == ("tsdb", "v13", "filesystem")


def test_loki_runs_expanding_the_environment_so_the_retention_variable_works(compose):
    command = compose["services"]["loki"]["command"]
    assert "-config.expand-env=true" in command and "-config.file=/etc/loki/config.yaml" in command


def test_the_three_parts_agree_on_where_loki_is(compose, loki):
    assert loki["server"]["http_listen_port"] == 3100
    assert 'url = "http://loki:3100/loki/api/v1/push"' in alloy_text()
    datasource = yaml.safe_load(DATASOURCE.read_text(encoding="utf-8"))["datasources"][0]
    assert datasource["url"] == "http://loki:3100"
    assert compose["services"]["loki"]["ports"][0].endswith(":3100")


def test_grafana_and_alloy_start_after_loki_and_cope_with_its_warm_up(compose):
    """Loki cannot be health-checked (see the next test), so they wait for it to start, not to be ready."""
    for name in ("grafana", "alloy"):
        assert compose["services"][name]["depends_on"]["loki"]["condition"] == "service_started"


def test_loki_has_no_healthcheck_because_its_image_has_no_shell(compose):
    """A real run showed the Loki image has no /bin/sh, wget or curl, so any healthcheck is
    permanently unhealthy and, with depends_on, stops the rest of the stack from starting."""
    assert "healthcheck" not in compose["services"]["loki"]


def test_the_alloy_healthcheck_uses_bash_and_ends_the_request_properly(compose):
    """Alloy's image has bash but no curl or wget. The request must end with a blank line, and the
    file must hold backslash sequences, not real line breaks (YAML would fold those into spaces)."""
    backslash, lf, cr = chr(92), chr(10), chr(13)
    crlf = backslash + "r" + backslash + "n"
    test = compose["services"]["alloy"]["healthcheck"]["test"]
    assert test[:3] == ["CMD", "bash", "-c"]
    command = test[3]
    assert "GET /-/ready HTTP/1.0" + crlf + crlf in command
    assert lf not in command and cr not in command, "real line breaks leaked into the command"
    assert "127.0.0.1/12345" in command, "it must probe Alloy's own port inside the container"


def test_the_grafana_datasource_is_provisioned_and_read_only():
    datasource = yaml.safe_load(DATASOURCE.read_text(encoding="utf-8"))["datasources"][0]
    assert (datasource["type"], datasource["uid"], datasource["isDefault"], datasource["editable"]) == ("loki", "loki", True, False)


def test_the_drop_folder_is_mounted_where_alloy_looks_and_git_ignores_it(compose):
    mounts = [v for v in compose["services"]["alloy"]["volumes"] if ":/var/log/riffle" in str(v)]
    assert mounts == ["${RIFFLE_LOGS_DIR:-../logs}:/var/log/riffle:ro"]
    config = alloy_text()
    assert '"/var/log/riffle/**/*.log"' in config and '"/var/log/riffle/**/*.jsonl"' in config
    assert (INFRA / "../logs").resolve() == REPO / "logs"
    assert "/logs/" in (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()


def test_alloy_sends_both_sources_to_loki_and_its_brackets_balance():
    config = alloy_text()
    assert config.count("forward_to = [loki.write.local.receiver]") == 2
    for source in ("loki.source.docker", "loki.source.file", "loki.write"):
        assert source in config
    code = re.sub(r"//.*", "", config)
    for opening, closing in (("{", "}"), ("[", "]"), ("(", ")")):
        assert code.count(opening) == code.count(closing), f"unbalanced {opening}{closing}"


def test_every_variable_the_compose_file_uses_is_documented_in_the_example():
    used = set(re.findall(r"\$\{([A-Z][A-Z0-9_]+)", COMPOSE.read_text(encoding="utf-8")))
    assert used, "expected the compose file to use variables"
    assert used <= env_example_names(), f"undocumented in .env.example: {sorted(used - env_example_names())}"


def test_the_example_file_holds_no_real_looking_secret():
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        if re.match(r"[A-Z_]*(PASSWORD|SECRET|TOKEN|KEY)[A-Z_]*=", line):
            assert line.split("=", 1)[1].strip() == "", f"a value is filled in: {line.split('=')[0]}"


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker is not installed")
def test_docker_compose_accepts_the_file(tmp_path):
    """`compose config` only parses; it needs no running engine."""
    empty_env = tmp_path / "empty.env"
    empty_env.write_text("", encoding="utf-8")
    result = subprocess.run(
        ["docker", "compose", "--env-file", str(empty_env), "-f", str(COMPOSE), "config", "--quiet"],
        capture_output=True, text=True, env={k: v for k, v in os.environ.items() if k != "GF_SECURITY_ADMIN_PASSWORD"},
    )
    assert result.returncode == 0, result.stderr


def test_docker_compose_parses_the_file_with_no_environment_at_all(tmp_path):
    """Nothing in the file may depend on a variable being set just to be read."""
    empty_env = tmp_path / "empty.env"
    empty_env.write_text("", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GRAFANA", "GF_", "LOKI", "LOGGING", "RIFFLE"))}
    result = subprocess.run(
        ["docker", "compose", "--env-file", str(empty_env), "-f", str(COMPOSE), "config", "--quiet"],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, result.stderr


def grafana_guard(compose) -> str:
    entrypoint = compose["services"]["grafana"]["entrypoint"]
    assert entrypoint[:2] == ["/bin/sh", "-c"], "an inline guard: a mounted script would get CRLF from a Windows checkout"
    return entrypoint[2].replace("$$", "$")  # Compose turns $$ into $ before the container sees it


def test_grafana_will_not_start_without_a_login_set_and_then_hands_over_to_grafana(compose):
    command = grafana_guard(compose)
    assert command.rstrip().endswith("exec /run.sh"), "after the check it must become Grafana's own start-up"
    assert "exit 1" in command and "GF_SECURITY_ADMIN_PASSWORD" in command


@pytest.mark.skipif(shutil.which("sh") is None, reason="no POSIX shell here")
def test_the_guard_really_refuses_an_empty_or_missing_password_and_lets_a_set_one_through(compose):
    """Runs the guard's shell code. `exec /run.sh` cannot run outside the Grafana image, so a set
    password ends in 'not found' (126/127), never in the guard's own exit 1 and message."""
    command = grafana_guard(compose)
    base = {k: v for k, v in os.environ.items() if k != "GF_SECURITY_ADMIN_PASSWORD"}
    for label, env in (("missing", base), ("empty", {**base, "GF_SECURITY_ADMIN_PASSWORD": ""})):
        result = subprocess.run(["sh", "-c", command], capture_output=True, text=True, env=env)
        assert result.returncode == 1 and "admin login is not set" in result.stderr, label
    result = subprocess.run(["sh", "-c", command], capture_output=True, text=True, env={**base, "GF_SECURITY_ADMIN_PASSWORD": "any-value"})
    assert "admin login is not set" not in result.stderr and result.returncode != 1
