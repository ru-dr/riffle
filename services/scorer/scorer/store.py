"""score_results in Postgres (infra/db/migrations): one row per delivery, append-only.

The scorer connects through the Cloud SQL Proxy sidecar (127.0.0.1:5432) as its IAM login, a member of riffle_app,
so row-level security applies: every transaction sets app.tenant_id first, and a forgotten tenant sees and writes
nothing. delivery_id is the key, so a redelivered message finds its row and is not scored twice.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import timezone

COLUMNS = ("delivery_id", "tenant_id", "repo", "pr_number", "head_sha", "trigger", "p_lands_clean",
           "p_lands_after_rework", "p_goes_nowhere", "rank_score", "rank_mode", "rank_band", "model_version",
           "miner_version", "layer_weight", "predictions", "explanation", "unranked_reason", "scored_at")
# The features column stays null: the model's inputs go to the score log, not Postgres (ADR 0024).
JSON_COLUMNS = ("predictions",)

INSERT = (f"INSERT INTO score_results ({', '.join(COLUMNS)}) VALUES ({', '.join(['%s'] * len(COLUMNS))}) "
          "ON CONFLICT (delivery_id) DO NOTHING")
SELECT = f"SELECT {', '.join(COLUMNS)} FROM score_results WHERE delivery_id = %s"
# The latest rank score of each other PR of the repo, recent ones first: what bands are cut from.
RECENT = """
SELECT rank_score FROM (
  SELECT DISTINCT ON (pr_number) pr_number, rank_score, scored_at FROM score_results
  WHERE tenant_id = %s AND repo = %s AND pr_number <> %s AND rank_score IS NOT NULL AND rank_mode = %s
    AND scored_at > now() - make_interval(days => %s)
  ORDER BY pr_number, scored_at DESC
) latest ORDER BY scored_at DESC LIMIT %s
"""


class Store:
    def __init__(self, conninfo: str):
        self.conninfo = conninfo
        self._local = threading.local()     # one connection per worker thread

    def _conn(self):
        import psycopg
        c = getattr(self._local, "conn", None)
        if c is None or c.closed or c.broken:
            c = self._local.conn = psycopg.connect(self.conninfo, autocommit=False, connect_timeout=10)
        return c

    def _run(self, tenant_id: str, fn):
        conn = self._conn()
        try:
            with conn.transaction():
                conn.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
                return fn(conn)
        except Exception:
            if conn.broken:
                self._local.conn = None
            raise

    def get(self, tenant_id: str, delivery_id: str) -> dict | None:
        row = self._run(tenant_id, lambda c: c.execute(SELECT, (delivery_id,)).fetchone())
        return _record(row) if row else None

    def insert(self, record: dict) -> bool:
        """True if this call wrote the row; False if the delivery was already stored."""
        values = tuple(_jsonb(record[c]) if c in JSON_COLUMNS else record[c] for c in COLUMNS)
        return self._run(record["tenant_id"], lambda c: c.execute(INSERT, values).rowcount == 1)

    def recent_rank_scores(self, tenant_id: str, repo: str, pr_number: int, rank_mode: str,
                           days: int, limit: int) -> list[float]:
        rows = self._run(tenant_id, lambda c: c.execute(
            RECENT, (tenant_id, repo, pr_number, rank_mode, days, limit)).fetchall())
        return [float(r[0]) for r in rows]


def _jsonb(v):
    if v is None:
        return None
    from psycopg.types.json import Jsonb
    return Jsonb(v)


def _record(row) -> dict:
    r = dict(zip(COLUMNS, row))
    if hasattr(r["scored_at"], "astimezone"):
        r["scored_at"] = r["scored_at"].astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    for c in JSON_COLUMNS:
        if isinstance(r[c], str):
            r[c] = json.loads(r[c])
    return r


def from_env() -> Store:
    host, port = os.environ.get("DB_HOST", "127.0.0.1"), os.environ.get("DB_PORT", "5432")
    user, db = os.environ["DB_USER"], os.environ["DB_NAME"]
    password = os.environ.get("DB_PASSWORD", "")      # empty in the cluster: the proxy logs in with IAM
    from psycopg.conninfo import make_conninfo
    return Store(make_conninfo(host=host, port=port, user=user, dbname=db, password=password or None,
                               sslmode=os.environ.get("DB_SSLMODE", "disable")))
