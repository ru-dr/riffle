"""The lead changes a split's shared settings in the store; joined PCs read them with the token they already have.
No token or join code is re-issued, and the PCs' finished rows are kept."""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
MINER = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", "pipelines", "ingestion", "repo_miner"))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "server"))
sys.path.insert(0, MINER)
os.environ.setdefault("RIFFLE_NO_DOCTOR", "1")
os.environ.setdefault("RIFFLE_NO_PLAN_USAGE", "1")
os.environ.setdefault("RIFFLE_DASHBOARD", "0")

import app as store_app  # noqa: E402
import sync  # noqa: E402
import tokens  # noqa: E402

from test_lead_settings import FLAGS, REPOS, issue, post  # noqa: E402,F401  (same fixtures and helpers)
from test_lead_settings import store  # noqa: E402,F401


NEW = FLAGS + ["--llm"]


def test_changing_settings_keeps_every_token_and_join_code(store):
    pcs = issue(store)
    before = tokens.load(store.tp)["pcs"]
    version = tokens.set_split_settings(store.tp, "706ba187", NEW)
    assert tokens.load(store.tp)["pcs"] == before                  # nothing re-issued
    assert tokens.lookup(store.tp, pcs[0]["token"])["split"] == "706ba187"
    assert version == tokens.settings_version(NEW) and version != tokens.settings_version(FLAGS)


def test_a_pc_with_its_old_token_reads_the_new_settings(store):
    pcs = issue(store)
    post(store, "/v1/admin/split-settings", {"split_id": "706ba187", "flags": NEW})
    meta = sync.fetch_split(store.url, pcs[1]["token"])
    assert meta["flags"] == NEW
    assert meta["settings_version"] == tokens.settings_version(NEW)


def test_only_an_admin_can_change_settings(store):
    pcs = issue(store)
    req = urllib.request.Request(store.url + "/v1/admin/split-settings", method="POST",
                                 data=json.dumps({"split_id": "706ba187", "flags": NEW}).encode(),
                                 headers={"Authorization": f"Bearer {pcs[0]['token']}", "Content-Type": "application/json"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req)
    assert e.value.code in (401, 403)
    assert tokens.load(store.tp)["splits"]["706ba187"]["flags"] == FLAGS


def test_an_unknown_split_and_bad_flags_are_refused(store):
    issue(store)
    with pytest.raises(LookupError):
        tokens.set_split_settings(store.tp, "nosuch", NEW)
    with pytest.raises(ValueError):
        tokens.set_split_settings(store.tp, "706ba187", "--llm")         # a string, not a list
    with pytest.raises(ValueError):
        tokens.set_split_settings(store.tp, "706ba187", ["x" * 501])


def test_an_unknown_split_over_http_is_404(store):
    issue(store)
    req = urllib.request.Request(store.url + "/v1/admin/split-settings", method="POST",
                                 data=json.dumps({"split_id": "nosuch", "flags": NEW}).encode(),
                                 headers={"Authorization": f"Bearer {store.adm}", "Content-Type": "application/json"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req)
    assert e.value.code == 404
