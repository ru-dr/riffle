"""Check AIRFLOW_USERS and AIRFLOW_PASSWORDS and write the simple auth manager's
password file. Runs in the init service, before the database migrates.

    AIRFLOW_USERS      alice:admin,bob:viewer      name:role, comma separated
    AIRFLOW_PASSWORDS  alice:<pw>,bob:<pw>         name:password, same names

Errors name the entry's position and never print a password."""
from __future__ import annotations

import json
import os
import sys

ROLES = ("admin", "op", "user", "viewer")
MIN_PASSWORD = 12
USERS_VAR = "AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_USERS"
PASSWORDS_VAR = "AIRFLOW_PASSWORDS"
PASSWORDS_FILE = "/opt/airflow/auth/passwords.json"


def parse_pairs(raw: str, var: str, second: str) -> dict[str, str]:
    """`a:b,c:d` as a dict. Entries are stripped, empty ones skipped (a trailing
    comma is fine), each must have exactly one ':' and a non-empty name and `second`."""
    out: dict[str, str] = {}
    for i, entry in enumerate((e.strip() for e in raw.split(",")), 1):
        if not entry:
            continue
        name, sep, value = entry.partition(":")
        name, value = name.strip(), value.strip()
        if not sep or ":" in value or not name or not value:
            raise SystemExit(f"{var}: entry {i} must look like name:{second} with exactly one ':'"
                             f"{' (a password may not contain \",\" or \":\")' if second == 'password' else ''}")
        if name in out:
            raise SystemExit(f"{var}: the name {name!r} appears twice")
        out[name] = value
    if not out:
        raise SystemExit(f"{var} lists nobody")
    return out


def validate(env: dict[str, str]) -> dict[str, str]:
    """The passwords by user name, or exit with a message that says what to fix."""
    users = parse_pairs(env.get(USERS_VAR, ""), "AIRFLOW_USERS", "role")
    passwords = parse_pairs(env.get(PASSWORDS_VAR, ""), PASSWORDS_VAR, "password")
    if set(users) != set(passwords):
        raise SystemExit("AIRFLOW_USERS and AIRFLOW_PASSWORDS must list the same users: "
                         f"only in USERS: {sorted(set(users) - set(passwords))}, "
                         f"only in PASSWORDS: {sorted(set(passwords) - set(users))}")
    bad = sorted({r for r in users.values() if r not in ROLES})
    if bad:
        raise SystemExit(f"unknown role(s) {bad}; roles must be one of {', '.join(ROLES)}")
    short = sorted(u for u, p in passwords.items() if len(p) < MIN_PASSWORD)
    if short:
        raise SystemExit(f"every password needs at least {MIN_PASSWORD} characters; too short for: {short}")
    return passwords


def main(env: dict[str, str] | None = None, path: str = PASSWORDS_FILE) -> None:
    passwords = validate(dict(os.environ if env is None else env))
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(passwords, fh)
    os.chmod(path, 0o600)


if __name__ == "__main__":
    main()
    sys.exit(0)
