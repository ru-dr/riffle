"""tenant_keys on a real Postgres with the real migrations, as a riffle_app login (row-level security on). Skipped
unless WRITER_TEST_DATABASE_URL points at an empty database as an admin:

    docker run -d --name pg -e POSTGRES_PASSWORD=pw -p 5432:5432 postgres:17
    WRITER_TEST_DATABASE_URL=postgresql://postgres:pw@localhost:5432/postgres python -m pytest tests
"""
import os

import pytest

from conftest import ROOT, FakeKms
from scorelog_writer import keys

URL = os.environ.get("WRITER_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set WRITER_TEST_DATABASE_URL to run against Postgres")
MIGRATIONS = sorted((ROOT / "infra" / "db" / "migrations").glob("*.sql"))


@pytest.fixture(scope="module")
def admin():
    import psycopg
    c = psycopg.connect(URL, autocommit=True)
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    c.execute("DROP ROLE IF EXISTS writer_test")
    c.execute("DROP ROLE IF EXISTS person_test")
    for m in MIGRATIONS:
        c.execute(m.read_text().split("-- migrate:down")[0].replace("-- migrate:up", ""))
    c.execute("CREATE ROLE writer_test LOGIN PASSWORD 'pw' IN ROLE riffle_app")
    c.execute("CREATE ROLE person_test LOGIN PASSWORD 'pw' IN ROLE riffle_readonly")
    c.execute("INSERT INTO tenants (tenant_id, account_login, account_type, status) VALUES "
              "('48213', 'acme-corp', 'Organization', 'active'), ('99', 'other', 'User', 'active'), "
              "('7', 'leaving', 'User', 'deleting')")
    yield c
    c.close()


@pytest.fixture(scope="module")
def db(admin):
    from psycopg.conninfo import make_conninfo
    return keys.Db(make_conninfo(URL, user="writer_test", password="pw"))


def test_a_key_is_made_once_and_read_back(db):
    kms = FakeKms()
    k1 = keys.KeyStore(db, kms, cache_seconds=0).data_key("48213")
    k2 = keys.KeyStore(db, kms, cache_seconds=0).data_key("48213")      # another writer
    assert k1 == k2 and len(k1) == 32 and kms.wraps == 1


def test_a_second_insert_keeps_the_first_key(db):
    first = db.get("48213")
    db.insert("48213", b"48213:other", FakeKms.kek_name)
    assert db.get("48213") == first


def test_another_tenant_cannot_see_the_key(db):
    keys.KeyStore(db, FakeKms(), cache_seconds=0).data_key("99")
    assert db._run("99", lambda c: c.execute("SELECT count(*) FROM tenant_keys").fetchone()[0]) == 1
    assert db._run("", lambda c: c.execute("SELECT count(*) FROM tenant_keys").fetchone()[0]) == 0


def test_a_tenant_being_deleted_gets_no_key(db):
    assert keys.KeyStore(db, FakeKms(), cache_seconds=0).data_key("7") is None and db.get("7") is None


def test_people_cannot_read_wrapped_keys(admin):
    import psycopg
    from psycopg.conninfo import make_conninfo
    with psycopg.connect(make_conninfo(URL, user="person_test", password="pw")) as c:
        c.execute("SET app.tenant_id = '48213'")
        assert c.execute("SELECT count(*) FROM score_results").fetchone()[0] == 0     # tables still readable
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("SELECT * FROM tenant_keys")


def test_services_cannot_change_or_delete_a_key(db):
    import psycopg
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        db._run("48213", lambda c: c.execute("UPDATE tenant_keys SET wrapped_key = 'x'"))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        db._run("48213", lambda c: c.execute("DELETE FROM tenant_keys"))


def test_deleting_the_tenant_deletes_its_key(admin, db):
    admin.execute("DELETE FROM tenants WHERE tenant_id = '99'")
    assert db.get("99") is None
