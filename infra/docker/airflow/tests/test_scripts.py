"""init_auth.py and db_url.py. Run: python -m pytest infra/docker/airflow/tests"""
from __future__ import annotations

import json
import os
import stat
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import db_url  # noqa: E402
import init_auth  # noqa: E402

GOOD = {"AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_USERS": "alice:admin,bob:viewer",
        "AIRFLOW_PASSWORDS": "alice:aaaaaaaaaaaa,bob:bbbbbbbbbbbb"}


def _env(**over):
    return {**GOOD, **over}


def test_valid_input_gives_passwords_by_user():
    assert init_auth.validate(GOOD) == {"alice": "aaaaaaaaaaaa", "bob": "bbbbbbbbbbbb"}


def test_trailing_comma_and_spaces_after_commas_are_fine():
    env = _env(AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_USERS="alice:admin, bob:viewer,",
               AIRFLOW_PASSWORDS="alice:aaaaaaaaaaaa, bob:bbbbbbbbbbbb,")
    assert set(init_auth.validate(env)) == {"alice", "bob"}


@pytest.mark.parametrize("passwords, expect", [
    ("alice:aaaa:aaaaaaaa,bob:bbbbbbbbbbbb", "entry 1 must look like name:password"),
    ("alice:aaaaaaaaaaaa,bob", "entry 2 must look like name:password"),
    ("alice:aaaaaaaaaaaa,bob:b,bbbbbbbbbbb", "entry 3 must look like name:password"),   # a comma inside a password
    ("alice:aaaaaaaaaaaa,alice:cccccccccccc", "appears twice"),
    ("alice:aaaaaaaaaaaa", "must list the same users"),
    ("alice:aaaaaaaaaaaa,bob:short", "at least 12 characters"),
    ("", "lists nobody"),
])
def test_bad_passwords_exit_with_a_clear_message_and_never_echo_a_password(passwords, expect):
    with pytest.raises(SystemExit) as e:
        init_auth.validate(_env(AIRFLOW_PASSWORDS=passwords))
    assert expect in str(e.value)
    assert "aaaaaaaaaaaa" not in str(e.value) and "bbbbbbbbbbbb" not in str(e.value)


def test_unknown_role_is_named():
    with pytest.raises(SystemExit) as e:
        init_auth.validate(_env(AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_USERS="alice:root,bob:viewer"))
    assert "'root'" in str(e.value) and "admin" in str(e.value)


def test_password_file_is_written_private(tmp_path):
    path = str(tmp_path / "passwords.json")
    init_auth.main(GOOD, path)
    assert json.load(open(path)) == {"alice": "aaaaaaaaaaaa", "bob": "bbbbbbbbbbbb"}
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_db_url_encodes_characters_that_break_a_url():
    url = db_url.build("p@ss/w:rd#1%")
    assert url == "postgresql+psycopg2://airflow:p%40ss%2Fw%3Ard%231%25@postgres/airflow"
    assert db_url.build("plain-token_123").endswith("airflow:plain-token_123@postgres/airflow")
