"""Audit fixes in the store: split generations, version order, finished runs, admin sessions, throttle, path conflicts."""
from __future__ import annotations

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "server"))

import app  # noqa: E402
import index as index_mod  # noqa: E402
import tables  # noqa: E402
import tokens  # noqa: E402


def test_plan_leaves_out_an_earlier_plan_of_the_split():
    keys = [f"pc{k}of{n}-abc/6.1.0/abc/data/dataset_pc{k}of{n}.parquet" for n in (2, 3) for k in range(1, n + 1)]
    out, notes = tables.plan(keys, "6.1.0", "abc", None, 3)
    assert [k.split("/")[0] for k in out["mined_dataset"]] == ["pc1of3-abc", "pc2of3-abc", "pc3of3-abc"]
    assert any("earlier plan" in n for n in notes)
    assert any("several plans" in n for n in tables.plan(keys, "6.1.0", "abc")[1])


def test_unsharded_dataset_is_a_dataset():
    assert tables.table_for("dataset.parquet") == "mined_dataset"
    assert tables.table_for("dataset_snapshots.parquet") == "mined_snapshot"


def test_versions_sort_numerically():
    assert sorted(["6.13.0", "6.9.1", "6.9.0"], key=tables.version_key) == ["6.9.0", "6.9.1", "6.13.0"]
    assert sorted(["6.1.0", "6.x"], key=tables.version_key)


def test_batch_done_with_failures_is_not_finished():
    assert tables.finished({"reason": "batch done"})
    assert not tables.finished({"reason": "batch done", "files_failed": 1})
    assert not tables.finished({"reason": "batch done", "files_deferred": 2})
    assert not tables.finished({"reason": "batch incomplete"})


def test_key_with_trailing_newline_is_refused():
    assert not app.valid_key("pc1/6.0.0/a.log\n")


def test_throttle_counts_one_token_once():
    t = app.Throttle(limit=3)
    for _ in range(10):
        t.fail("ip", "revoked")
    assert not t.blocked("ip")
    for i in range(3):
        t.fail("ip", f"guess{i}")
    assert t.blocked("ip")


def test_admin_session_is_signed_and_dies_with_the_token(tmp_path):
    p = str(tmp_path / "tokens.json")
    tok = tokens.issue(p, "admins", "lead")
    s = tokens.session_make(p, "lead")
    assert tok not in s and tokens.session_who(p, s) == ("admins", "lead")
    assert tokens.session_who(p, s[:-1] + ("0" if s[-1] != "0" else "1")) is None
    assert tokens.session_who(p, tokens.session_make(p, "lead", ttl=-5)) is None
    tokens.issue(p, "admins", "lead")                      # re-issued: old sessions end
    assert tokens.session_who(p, s) is None


def test_last_admin_cannot_be_revoked(tmp_path):
    p = str(tmp_path / "tokens.json")
    tokens.issue(p, "admins", "a")
    with pytest.raises(ValueError):
        tokens.revoke_kind(p, "admins", "a")
    tokens.issue(p, "admins", "b")
    assert tokens.revoke_kind(p, "admins", "a")


def test_like_escape_is_literal(tmp_path):
    ix = index_mod.Index(str(tmp_path / "i.db"))
    ix.put_file("pc1/a_b/x", "pc1", 1, None)
    ix.put_file("pc1/aXb/x", "pc1", 1, None)
    assert [f["key"] for f in ix.files(like=index_mod.like_escape("pc1/a_b") + "/%")] == ["pc1/a_b/x"]


def test_a_file_and_a_directory_cannot_share_a_path(tmp_path):
    import io
    svc = app.Service(str(tmp_path))
    assert svc.put("pc1", "6.0.0/s/logs/a", io.BytesIO(b"x"), 1, None)[0] == 200
    assert svc.put("pc1", "6.0.0/s/logs/a/b", io.BytesIO(b"x"), 1, None)[0] == 409
    assert svc.put("pc1", "6.0.0/s/x/b", io.BytesIO(b"x"), 1, None)[0] == 200
    assert svc.put("pc1", "6.0.0/s/x", io.BytesIO(b"x"), 1, None)[0] == 409


def test_run_json_batch_done_with_failed_files_is_stored_incomplete(tmp_path):
    import io
    import json
    svc = app.Service(str(tmp_path))
    body = json.dumps({"reason": "batch done", "files_failed": 2}).encode()
    assert svc.put("pc1", "6.0.0/s/run.json", io.BytesIO(body), len(body), None)[0] == 200
    assert svc.index.run("pc1", "6.0.0/s")["status"]["reason"] == "batch incomplete"
