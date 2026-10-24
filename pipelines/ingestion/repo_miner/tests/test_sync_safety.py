"""sync.py: a partial upload is never reported as "batch done"; http and cross-origin redirects are refused."""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sync  # noqa: E402
from test_sync import Store, run_dir, store  # noqa: E402,F401


def _final(run_dir, status, **kw):
    return sync.sync(str(run_dir / "dataset_pc1of2.jsonl"), "6.6.0/abc", status,
                     health_dir=str(run_dir / "health"),
                     logs=[str(run_dir / "logs" / "run-1.log")],
                     state_path=str(run_dir / "state.json"), **kw)


def test_batch_done_sent_when_everything_went(store, run_dir):
    _final(run_dir, {"reason": "batch done"})
    assert json.loads(Store.puts["6.6.0/abc/run.json"])["reason"] == "batch done"


def test_batch_done_downgraded_when_a_file_failed(store, run_dir):
    (run_dir / "logs" / "run-1.log").write_text("x" * 100)
    r = _final(run_dir, {"reason": "batch done"}, max_bytes=10)       # the log is left out for size
    assert r["skipped"] >= 1
    doc = json.loads(Store.puts["6.6.0/abc/run.json"])
    assert doc["reason"] == "batch incomplete" and doc["files_deferred"] >= 1


def test_http_to_another_host_is_off(monkeypatch):
    monkeypatch.setenv("RIFFLE_SYNC_URL", "http://store.example.com")
    monkeypatch.setenv("RIFFLE_SYNC_TOKEN", "t")
    assert sync.settings() is None
    monkeypatch.setenv("RIFFLE_SYNC_URL", "https://store.example.com/")
    assert sync.settings() == ("https://store.example.com", "t")


def test_redirect_to_another_origin_refused():
    import urllib.error
    import urllib.request
    h = sync._SameHostRedirect()
    req = urllib.request.Request("https://a.example/x", headers={"Authorization": "Bearer t"})
    try:
        h.redirect_request(req, None, 302, "Found", {}, "https://evil.example/y")
    except urllib.error.HTTPError:
        return
    raise AssertionError("cross-origin redirect was followed")
