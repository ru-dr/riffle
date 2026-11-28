"""score_results on a real Postgres with the real migrations, connected as a riffle_app login like the scorer in the
cluster (row-level security on). Skipped unless SCORER_TEST_DATABASE_URL points at an empty database as an admin:

    docker run -d --name pg -e POSTGRES_PASSWORD=pw -p 5432:5432 postgres:17
    SCORER_TEST_DATABASE_URL=postgresql://postgres:pw@localhost:5432/postgres python -m pytest tests
"""
import json
import os

import pytest

from conftest import ROOT, FakePublisher, FakeHistory
from scorer import models, predict, scoring, store

URL = os.environ.get("SCORER_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set SCORER_TEST_DATABASE_URL to run against Postgres")
MIGRATIONS = sorted((ROOT / "infra" / "db" / "migrations").glob("*.sql"))


@pytest.fixture(scope="module")
def db():
    import psycopg
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        c.execute("DROP ROLE IF EXISTS scorer_test")
        for m in MIGRATIONS:
            c.execute(m.read_text().split("-- migrate:down")[0].replace("-- migrate:up", ""))
        c.execute("CREATE ROLE scorer_test LOGIN PASSWORD 'pw' IN ROLE riffle_app")
        c.execute("INSERT INTO tenants (tenant_id, account_login, account_type) VALUES "
                  "('48213', 'acme-corp', 'Organization'), ('99', 'other', 'User')")
    yield store.Store(make_conninfo(URL, user="scorer_test", password="pw"))


def test_a_scored_pr_is_stored_once_and_read_back(db, message, tiny_model_dir):
    loaded = models.Loaded("riffle-outcome-setup-g/7/new_repos", "7", predict.Variant(tiny_model_dir), "7.2.2")
    pub = FakePublisher()
    s = scoring.Scorer(models=type("M", (), {"current": loaded})(), history=FakeHistory(), store=db,
                       publisher=pub, contracts=scoring.Contracts())
    assert s.handle(json.dumps(message).encode()) == "ranked"
    first = pub.sent[0][1]
    pub.sent.clear()
    assert s.handle(json.dumps(message).encode()) == "duplicate"      # read back from Postgres and republished
    again = pub.sent[0][1]
    assert again == first
    assert len(pub.sent[1][1]["features"]) == 161


def test_unranked_rows_meet_the_tables_rule(db, message):
    msg = {**message, "delivery_id": "unranked-1"}
    s = scoring.Scorer(models=type("M", (), {"current": None})(), history=FakeHistory(), store=db,
                       publisher=FakePublisher(), contracts=scoring.Contracts())
    assert s.handle(json.dumps(msg).encode()) == "model_unavailable"
    assert db.get("48213", "unranked-1")["unranked_reason"] == "model_unavailable"


def test_another_tenant_sees_nothing(db, message):
    assert db.get("99", message["delivery_id"]) is None
    assert db.recent_rank_scores("99", message["repo"], 1, "merge_first", 90, 100) == []


def test_recent_scores_are_the_latest_per_other_pr(db, message):
    base = scoring.base_record(message, "2026-11-27T12:00:00Z")
    for i, (pr, score) in enumerate([(50, 0.2), (50, 0.4), (51, 0.9)]):
        db.insert({**base, "delivery_id": f"r{i}", "pr_number": pr, "rank_score": score, "rank_band": "standard",
                   "model_version": "m", "scored_at": f"2026-11-27T12:00:0{i}Z"})
    got = db.recent_rank_scores("48213", message["repo"], 51, "merge_first", 36500, 100)
    assert 0.4 in got and 0.2 not in got and 0.9 not in got     # PR 50's latest only; PR 51 is the one being scored
