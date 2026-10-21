"""The admin page: its own sign-in (admin tokens only), the overview, revoke, rotate with fresh codes, readers,
and the backup of tokens.json to the bucket with restore on a new disk."""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "server"))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", "pipelines", "ingestion", "repo_miner")))

import app  # noqa: E402
import backup  # noqa: E402
import join  # noqa: E402
import sync  # noqa: E402
import tokens  # noqa: E402

FLAGS = ["--repos-file", "repos_web_ts.txt", "--only-domain", "web-ts", "--max-prs", "8000"]


@pytest.fixture
def srv(tmp_path):
    tp = os.path.join(str(tmp_path), "tokens.json")
    adm, rdr = tokens.issue(tp, "admins", "lead"), tokens.issue(tp, "readers", "lead")
    s = app.serve(str(tmp_path), "127.0.0.1", 0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    s.url, s.adm, s.rdr, s.tp, s.dir = f"http://127.0.0.1:{s.server_port}", adm, rdr, tp, str(tmp_path)
    yield s
    s.shutdown()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def call(srv, method, path, body=None, bearer=None, cookie=None, headers=None, form=None):
    h = dict(headers or {})
    if bearer:
        h["Authorization"] = f"Bearer {bearer}"
    if cookie:
        h["Cookie"] = cookie
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        h["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(srv.url + path, data=data, method=method, headers=h)
    try:
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=10) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


import urllib.parse  # noqa: E402


def admin_cookie(srv):
    code, _b, hdr = call(srv, "POST", "/admin/login", form={"token": srv.adm})
    assert code == 302 and hdr["Location"] == "/admin"
    return hdr["Set-Cookie"].split(";")[0]


XHR = {"X-Requested-With": "riffle"}


def test_admin_has_its_own_sign_in_that_only_admins_pass(srv):
    code, _b, hdr = call(srv, "GET", "/admin")
    assert code == 302 and hdr["Location"] == "/admin/login"
    assert b'action="/admin/login"' in call(srv, "GET", "/admin/login")[1]
    code, body, _h = call(srv, "POST", "/admin/login", form={"token": srv.rdr})                  # a read token is not enough
    assert code == 401 and b"admin token" in body and srv.rdr.encode() not in body
    code, _b, hdr = call(srv, "POST", "/admin/login", form={"token": srv.adm})
    assert "HttpOnly" in hdr["Set-Cookie"] and "SameSite=Strict" in hdr["Set-Cookie"] and "riffle_admin=" in hdr["Set-Cookie"]
    ck = hdr["Set-Cookie"].split(";")[0]
    page = call(srv, "GET", "/admin", cookie=ck)[1]
    assert b'data-page="admin"' in page and b"<script>" not in page
    assert call(srv, "GET", "/admin/logout")[0] == 302
    # the two sessions do not mix: the admin cookie reads nothing, the reader cookie cannot administer
    assert call(srv, "GET", "/api/runs", cookie=ck)[0] == 401
    code, _b, hdr = call(srv, "POST", "/login", form={"token": srv.rdr})
    rck = hdr["Set-Cookie"].split(";")[0]
    assert call(srv, "GET", "/v1/admin/overview", cookie=rck)[0] == 401 and call(srv, "GET", "/admin", cookie=rck)[0] == 302


def test_cookie_calls_need_our_page_header_but_scripts_with_a_bearer_do_not(srv):
    ck = admin_cookie(srv)
    assert call(srv, "POST", "/v1/admin/splits", {"split_id": "ab12cd34", "n": 1}, cookie=ck)[0] == 403         # no X-Requested-With
    assert call(srv, "POST", "/v1/admin/splits", {"split_id": "ab12cd34", "n": 1}, cookie=ck, headers=XHR)[0] == 200
    assert call(srv, "POST", "/v1/admin/splits", {"split_id": "ee55ff66", "n": 1}, bearer=srv.adm)[0] == 200
    assert call(srv, "POST", "/v1/admin/splits", {"split_id": "x", "n": 1})[0] == 401                           # nobody: 401, not 403


def test_overview_states_splits_and_who_can_read(srv):
    pcs = call(srv, "POST", "/v1/admin/splits", {"split_id": "ab12cd34", "n": 3, "flags": FLAGS, "miner_version": "6.9.0"}, bearer=srv.adm)
    toks = json.loads(pcs[1])["pcs"]
    put = lambda t, key, body: urllib.request.urlopen(urllib.request.Request(  # noqa: E731
        srv.url + "/v1/files/" + key, data=body, method="PUT", headers={"Authorization": f"Bearer {t}"})).read()
    put(toks[0]["token"], "6.9.0/ab12cd34/run.json", json.dumps({"reason": "repo done", "host": "h1", "repos_done": 2, "repos_total": 4, "prs_done": 50}).encode())
    put(toks[1]["token"], "6.9.0/ab12cd34/run.json", json.dumps({"reason": "batch done"}).encode())
    d = json.loads(call(srv, "GET", "/v1/admin/overview", bearer=srv.adm)[1])
    by = {p["pc"]: p for p in d["pcs"]}
    assert by["pc1of3-ab12cd34"]["state"] == "mining" and by["pc1of3-ab12cd34"]["repos_total"] == 4 and by["pc1of3-ab12cd34"]["host"] == "h1"
    assert by["pc2of3-ab12cd34"]["state"] == "done" and by["pc3of3-ab12cd34"]["state"] == "never"
    sp = d["splits"][0]
    assert sp["split_id"] == "ab12cd34" and sp["n"] == 3 and sp["tokens"] == 3 and sp["uploaded"] == 2 and sp["flags"] == FLAGS and sp["can_rotate"]
    assert [r["name"] for r in d["readers"]] == ["lead"] and [a["name"] for a in d["admins"]] == ["lead"]
    assert "token" not in json.dumps(d).replace("tokens", "")                   # hashes and tokens are never in the overview
    # an expired token shows as expired
    tokens.issue(srv.tp, "pcs", "oldpc", split="ab12cd34", days=0.000001)
    time.sleep(0.2)
    assert {p["pc"]: p["state"] for p in json.loads(call(srv, "GET", "/v1/admin/overview", bearer=srv.adm)[1])["pcs"]}["oldpc"] == "expired"


def test_rotate_makes_new_codes_the_miner_can_use_and_kills_the_old_ones(srv):
    old = json.loads(call(srv, "POST", "/v1/admin/splits", {"split_id": "ab12cd34", "n": 2, "flags": FLAGS, "miner_version": "6.9.0", "days": 30}, bearer=srv.adm)[1])["pcs"]
    ck = admin_cookie(srv)
    code, body, _h = call(srv, "POST", "/v1/admin/rotate", {"split_id": "ab12cd34"}, cookie=ck, headers=dict(XHR, Host="data.example.org"))
    assert code == 200
    new = json.loads(body)["pcs"]
    assert [p["pc"] for p in new] == ["pc1of2-ab12cd34", "pc2of2-ab12cd34"] and all("token" not in p for p in new)    # only codes are returned
    doc = join.decode(new[1]["code"])                                            # the miner's own decoder reads the server's code
    assert (doc["k"], doc["n"], doc["s"], doc["v"], doc["a"]) == (2, 2, "ab12cd34", "6.9.0", FLAGS)
    assert doc["u"] == "http://data.example.org"                                 # the address it was reached on
    assert sync.whoami(srv.url, doc["t"])["name"] == "pc2of2-ab12cd34"
    assert not sync.whoami(srv.url, old[0]["token"])["ok"] and not sync.whoami(srv.url, old[1]["token"])["ok"]
    assert tokens.load(srv.tp)["pcs"]["pc1of2-ab12cd34"]["expires"]              # the 30 days carry over
    assert call(srv, "POST", "/v1/admin/rotate", {"split_id": "nope"}, cookie=ck, headers=XHR)[0] == 404


def test_server_and_miner_build_the_same_join_code():
    a = app.join_code("https://s.example", "rw_t", "ab12cd34", 2, 3, join.__version__, FLAGS)
    b = join.encode("https://s.example", "rw_t", "ab12cd34", 2, 3, FLAGS)
    assert a == b


def test_revoke_by_kind_and_adding_readers(srv):
    ck = admin_cookie(srv)
    tokens.issue(srv.tp, "readers", "maria")
    # both a reader and an admin are called "lead": revoking the reader keeps the admin
    assert json.loads(call(srv, "POST", "/v1/admin/revoke", {"name": "lead", "kind": "readers"}, cookie=ck, headers=XHR)[1])["revoked"] == ["lead"]
    assert tokens.who(srv.tp, srv.adm) == ("admins", "lead") and tokens.who(srv.tp, srv.rdr) is None
    code, body, _h = call(srv, "POST", "/v1/admin/readers", {"name": "sam"}, cookie=ck, headers=XHR)
    tok = json.loads(body)["token"]
    assert code == 200 and tok.startswith("rd_") and call(srv, "GET", "/api/runs", bearer=tok)[0] == 200
    assert call(srv, "POST", "/v1/admin/readers", {"name": "Bad Name"}, cookie=ck, headers=XHR)[0] == 400


# -- backup and restore -------------------------------------------------------

class FakeBlob:
    def __init__(self, bucket, name):
        self.bucket, self.name = bucket, name

    @property
    def size(self):
        return len(self.bucket.objects.get(self.name, b""))

    def exists(self):
        return self.name in self.bucket.objects

    def upload_from_filename(self, path):
        self.bucket.objects[self.name] = open(path, "rb").read()

    def download_to_filename(self, path):
        open(path, "wb").write(self.bucket.objects[self.name])


class FakeBucket:
    def __init__(self):
        self.objects = {}

    def blob(self, name):
        return FakeBlob(self, name)

    def list_blobs(self):
        return [FakeBlob(self, n) for n in sorted(self.objects)]


def test_tokens_are_backed_up_to_the_bucket_and_restored_on_a_new_disk(tmp_path):
    bucket = FakeBucket()
    d1 = tmp_path / "vm1"
    d1.mkdir()
    s1 = app.Service(str(d1))
    s1.store._bucket = bucket
    pcs = tokens.issue_split(s1.tokens_path, "ab12cd34", 2, flags=FLAGS, version="6.9.0")
    admin = tokens.issue(s1.tokens_path, "admins", "lead")
    s1.tokens_changed()
    assert "_system/tokens.json" in bucket.objects and pcs[0]["token"].encode() not in bucket.objects["_system/tokens.json"]   # hashes only
    # the disk is lost: a new VM starts empty and gets its tokens back
    d2 = tmp_path / "vm2"
    d2.mkdir()
    store2 = app.store_mod.Store(str(d2))
    store2._bucket = bucket
    assert backup.pull(store2, str(d2 / "tokens.json")) is True
    assert tokens.who(str(d2 / "tokens.json"), admin) == ("admins", "lead")
    assert tokens.who(str(d2 / "tokens.json"), pcs[1]["token"])[1] == "pc2of2-ab12cd34"
    assert tokens.load(str(d2 / "tokens.json"))["splits"]["ab12cd34"]["flags"] == FLAGS
    assert oct(os.stat(d2 / "tokens.json").st_mode & 0o777) == "0o600"
    assert backup.pull(store2, str(d2 / "tokens.json")) is False              # never overwrites a disk that has tokens


def test_restore_rebuilds_the_index_and_the_small_files_from_the_bucket(tmp_path):
    bucket = FakeBucket()
    bucket.objects.update({
        "pc1/6.9.0/abc/data/dataset_pc1of1.parquet": b"PAR1" * 100,
        "pc1/6.9.0/abc/run.json": json.dumps({"reason": "batch done", "repos_done": 3, "repos_total": 3}).encode(),
        "pc1/6.9.0/abc/logs/run-1.log": b"hello\n",
        "pc1/6.9.0/abc/health/o_a.json": b'{"repo":"o/a","flags":[]}',
        "_system/tokens.json": b"{}"})
    svc = app.Service(str(tmp_path))
    svc.store._bucket = bucket
    r = backup.restore_all(svc.store, svc.index)
    assert r == {"files": 4, "runs": 1, "mirrored": 3}
    assert svc.index.run("pc1", "6.9.0/abc")["status"]["reason"] == "batch done"
    assert svc.index.file("pc1/6.9.0/abc/data/dataset_pc1of1.parquet")["size"] == 400
    assert open(svc.store.local_path("pc1/6.9.0/abc/logs/run-1.log"), "rb").read() == b"hello\n"
    assert not os.path.exists(svc.store.local_path("pc1/6.9.0/abc/data/dataset_pc1of1.parquet"))   # big files stay in the bucket
    assert backup.restore_all(svc.store, svc.index)["mirrored"] == 0           # idempotent: nothing downloaded twice
