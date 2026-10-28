"""Print the SQLAlchemy URL for Airflow's metadata database.

Airflow runs this through AIRFLOW__DATABASE__SQL_ALCHEMY_CONN_CMD. Building the
URL here, not in compose.yaml, means a POSTGRES_PASSWORD with `@ / : # %` is
percent-encoded instead of breaking the URL, and no `user:password@host` string
sits in a committed file."""
from __future__ import annotations

import os
import sys
from urllib.parse import quote

USER, HOST, DATABASE = "airflow", "postgres", "airflow"


def build(password: str) -> str:
    return f"postgresql+psycopg2://{USER}:{quote(password, safe='')}@{HOST}/{DATABASE}"


if __name__ == "__main__":
    password = os.environ.get("POSTGRES_PASSWORD")
    if not password:
        sys.exit("POSTGRES_PASSWORD is not set")
    print(build(password), end="")     # no newline: Airflow 3.3 keeps it, and it would end up in the database name
