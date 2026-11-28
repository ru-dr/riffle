"""score-log messages to encrypted Parquet: batching, acknowledgement, keys, the file format."""
import io

import pyarrow.parquet as pq
import pytest
from cryptography.exceptions import InvalidTag

from conftest import ROOT, ROW, Bucket, FakeDb, FakeKms, Msg, row
from flatten import unflatten
from scorelog_writer import crypto, files, keys, writer


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


@pytest.fixture
def setup(validator):
    db, kms, bucket, clock = FakeDb(), FakeKms(), Bucket(), Clock()
    ks = keys.KeyStore(db, kms, now=clock)
    w = writer.Writer(ks, bucket, validator, batch_rows=3, flush_seconds=60, now=clock)
    return w, db, kms, bucket, clock, ks


def read_back(bucket, ks, name):
    tenant = name.split("tenant=")[1].split("/")[0]
    return pq.read_table(io.BytesIO(crypto.decrypt(bucket.objects[name], ks.data_key(tenant), name)))


def test_a_full_batch_is_written_encrypted_and_then_acknowledged(setup, validator):
    w, db, kms, bucket, clock, ks = setup
    msgs = [Msg(row(delivery_id=f"d-{i}", scored_at=f"2026-11-27T12:00:0{i}.000Z")) for i in range(3)]
    for m in msgs[:2]:
        w.add(m)
    assert bucket.objects == {} and all(m.done is None for m in msgs[:2])     # held until the file is stored
    w.add(msgs[2])
    [name] = bucket.objects
    assert name.startswith("score_log/tenant=48213/date=2026-11-27/120000-") and name.endswith(".parquet.enc")
    assert not bucket.objects[name].startswith(b"PAR1")                       # encrypted, not plain Parquet
    t = read_back(bucket, ks, name)
    assert t.num_rows == 3 and "/features/LA" in t.column_names and "/rank_score" in t.column_names
    assert all(m.done == "ack" for m in msgs)
    assert kms.wraps == 1                                                      # one key for the tenant, cached


def test_flat_columns_rebuild_the_message(setup):
    """Columns are the message's JSON Pointers (ADR 0012), so contracts/flatten.py's unflatten rebuilds it."""
    import json
    w, db, kms, bucket, clock, ks = setup
    for i in range(3):
        w.add(Msg(row(delivery_id=f"d-{i}")))
    [name] = bucket.objects
    first = {k: v[0] for k, v in read_back(bucket, ks, name).to_pydict().items()}
    schema = json.loads((ROOT / "contracts" / "score_log.schema.json").read_text(encoding="utf-8"))
    assert unflatten(first, schema) == {**ROW, "delivery_id": "d-0"}


def test_a_quiet_buffer_is_written_after_flush_seconds(setup):
    w, db, kms, bucket, clock, ks = setup
    m = Msg(row())
    w.add(m)
    clock.t = 59
    assert w.flush() == 0 and m.done is None
    clock.t = 60
    assert w.flush() == 1 and m.done == "ack" and len(bucket.objects) == 1


def test_one_file_per_tenant_and_day(setup):
    w, db, kms, bucket, clock, ks = setup
    db.statuses["777"] = "active"
    w.add(Msg(row(delivery_id="a")))
    w.add(Msg(row(delivery_id="b", scored_at="2026-11-28T00:00:01.000Z")))
    w.add(Msg(row(delivery_id="c", tenant_id="777")))
    w.flush(everything=True)
    dirs = sorted(n.rsplit("/", 1)[0] for n in bucket.objects)
    assert dirs == ["score_log/tenant=48213/date=2026-11-27", "score_log/tenant=48213/date=2026-11-28",
                    "score_log/tenant=777/date=2026-11-27"]


def test_a_failed_write_is_retried_not_lost(validator):
    db, kms, bucket = FakeDb(), FakeKms(), Bucket(fail=True)
    w = writer.Writer(keys.KeyStore(db, kms), bucket, validator, batch_rows=1)
    m = Msg(row())
    w.add(m)
    assert m.done == "nack" and bucket.objects == {}


def test_kms_down_is_retried(validator):
    class DownKms(FakeKms):
        def wrap(self, *a):
            raise ConnectionError("kms down")
    w = writer.Writer(keys.KeyStore(FakeDb(), DownKms()), Bucket(), validator, batch_rows=1)
    m = Msg(row())
    w.add(m)
    assert m.done == "nack"


@pytest.mark.parametrize("status", ["deleting", None])
def test_a_tenant_being_deleted_gets_no_key_and_its_rows_are_dropped(validator, status):
    db = FakeDb(statuses={"48213": status} if status else {})
    bucket = Bucket()
    w = writer.Writer(keys.KeyStore(db, FakeKms()), bucket, validator, batch_rows=1)
    m = Msg(row())
    w.add(m)
    assert m.done == "ack" and bucket.objects == {} and db.keys == {}


def test_a_suspended_tenant_still_gets_its_rows(validator):
    bucket = Bucket()
    w = writer.Writer(keys.KeyStore(FakeDb({"48213": "suspended"}), FakeKms()), bucket, validator, batch_rows=1)
    w.add(Msg(row()))
    assert len(bucket.objects) == 1


@pytest.mark.parametrize("data", [b"not json", b"[]", row(rank_band="top")])
def test_an_invalid_row_is_acknowledged_and_counted(setup, data):
    w, db, kms, bucket, clock, ks = setup
    before = writer.ROWS.labels("invalid")._value.get()
    m = Msg(data)
    w.add(m)
    assert m.done == "ack" and w.buffered() == 0
    assert writer.ROWS.labels("invalid")._value.get() == before + 1


def test_the_same_delivery_twice_is_one_row(setup):
    w, db, kms, bucket, clock, ks = setup
    msgs = [Msg(row(delivery_id="same")), Msg(row(delivery_id="same")), Msg(row(delivery_id="other"))]
    for m in msgs:
        w.add(m)
    [name] = bucket.objects
    assert read_back(bucket, ks, name).num_rows == 2 and all(m.done == "ack" for m in msgs)


def test_an_unranked_row_with_no_features_is_stored(setup):
    w, db, kms, bucket, clock, ks = setup
    w.add(Msg(row(delivery_id="u", rank_score=None, rank_band=None, unranked_reason="model_unavailable",
                  model_version=None, features={}, p_lands_clean=None, p_lands_after_rework=None,
                  p_goes_nowhere=None)))
    w.add(Msg(row(delivery_id="r1")))
    w.add(Msg(row(delivery_id="r2")))
    [name] = bucket.objects
    t = read_back(bucket, ks, name).to_pydict()
    i = t["/delivery_id"].index("u")
    assert t["/unranked_reason"][i] == "model_unavailable" and t["/features/LA"][i] is None


def test_a_file_only_opens_with_its_key_and_its_name():
    key, other = crypto.new_key(), crypto.new_key()
    blob = crypto.encrypt(b"rows", key, "score_log/tenant=1/date=2026-11-27/a.parquet.enc")
    assert crypto.decrypt(blob, key, "score_log/tenant=1/date=2026-11-27/a.parquet.enc") == b"rows"
    with pytest.raises(InvalidTag):
        crypto.decrypt(blob, other, "score_log/tenant=1/date=2026-11-27/a.parquet.enc")
    with pytest.raises(InvalidTag):         # moved to another tenant's folder
        crypto.decrypt(blob, key, "score_log/tenant=2/date=2026-11-27/a.parquet.enc")
    with pytest.raises(InvalidTag):
        crypto.decrypt(blob[:-1] + bytes([blob[-1] ^ 1]), key, "score_log/tenant=1/date=2026-11-27/a.parquet.enc")


def test_keys_are_made_once_per_tenant_and_bound_to_it():
    db, kms = FakeDb({"1": "active", "2": "active"}), FakeKms()
    ks = keys.KeyStore(db, kms, cache_seconds=0)
    k1, again, k2 = ks.data_key("1"), ks.data_key("1"), ks.data_key("2")
    assert k1 == again != k2 and len(k1) == 32 and kms.wraps == 2
    db.keys["2"] = db.keys["1"]                          # a key copied to another tenant does not unwrap
    with pytest.raises(PermissionError):
        ks.data_key("2")


def test_keys_are_cached():
    db = FakeDb()
    calls = []
    db.get = lambda t, _g=db.get: calls.append(t) or _g(t)
    clock = Clock()
    ks = keys.KeyStore(db, FakeKms(), cache_seconds=600, now=clock)
    ks.data_key("48213")
    n = len(calls)
    ks.data_key("48213")
    assert len(calls) == n
    clock.t = 601
    ks.data_key("48213")
    assert len(calls) > n
