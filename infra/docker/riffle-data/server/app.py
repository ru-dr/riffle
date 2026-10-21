"""
app.py — the team store's HTTP service (ADR 0015). Standard library only, apart
from google-cloud-storage (RIFFLE_BUCKET) and duckdb (the parquet viewer).

  PUT  /v1/files/<key>      a PC's token; stored as <pc>/<key> (a split-bound token: only under its split)
  GET  /v1/whoami           any token: who it is, its split and expiry (the miner's join check)
  POST /v1/admin/splits     admin token: {"split_id", "n", "days"} mints one PC token per PC
  POST /v1/admin/revoke     admin token: {"name"} or {"split_id"}
  GET  /v1/admin/pcs        admin token: the PC tokens and when each last uploaded
  GET  /healthz             open
  GET  /login, /logout      the pages' sign-in (the read token, kept in a cookie)
  GET  /, /run, /data, /view, /search     the pages (static/app.js fills them)
  GET  /api/...             JSON for the pages and for scripts (Bearer read token)
  GET  /static/...          the stylesheet, scripts, fonts

A PC's token can write only; a reader's can only read. Failed sign-ins are
throttled per client address. Behind a TLS proxy set RIFFLE_TRUST_PROXY=1 so the
client address and https come from X-Forwarded-*. Nothing here is on the scoring path.
"""
from __future__ import annotations

import base64
import hashlib
import http.cookies
import http.server
import json
import mimetypes
import os
import re
import sys
import tempfile
import threading
import time
from urllib.parse import parse_qs, unquote, urlparse

import backup
import index as index_mod
import store as store_mod
import tables
import tokens
import ui
import views

KEY = re.compile(r"^[A-Za-z0-9._-]{1,120}(/[A-Za-z0-9._-]{1,120}){1,7}$")
WORD = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
MAX_FILE = int(os.environ.get("RIFFLE_MAX_FILE_BYTES", 2 * 1024 ** 3))
TRUST_PROXY = os.environ.get("RIFFLE_TRUST_PROXY") == "1"
SEARCH_MAX_HITS = 300
SEARCH_MAX_BYTES = 400 * 1024 ** 2
SEARCH_BUDGET_S = 5.0
SEARCH_LINE = 2000                                          # characters of a log line a pattern is tried on
SOCKET_TIMEOUT_S = 120
MAX_RUN_JSON = 1024 * 1024
POST_CAP = {"/v1/admin/splits": 400_000}                    # a repo list is up to tokens.MAX_REPOS_TEXT, JSON-escaped
POST_CAP_DEFAULT = 32768
NESTED_QUANTIFIER = re.compile(r"[+*}]\)?[+*{]|\([^)]*[+*][^)]*\)[+*{]")
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
COOKIE = "riffle_session"
ADMIN_COOKIE = "riffle_admin"          # the admin page's own session: an admin token, never a reader's


def join_code(url: str, token: str, split: str, k: int, n: int, version: str | None, argv: list[str]) -> str:
    """A join code, in the format of the miner's join.py (a test keeps the two in step), so the admin
    page can rebuild a split's codes after a rotation."""
    doc = {"u": url, "t": token, "s": split, "k": k, "n": n, "v": version, "a": argv}
    return "riffle1." + base64.urlsafe_b64encode(json.dumps(doc, separators=(",", ":")).encode()).decode().rstrip("=")


def valid_key(key: str) -> bool:
    return bool(KEY.match(key)) and len(key) <= 400 and all(p not in (".", "..") for p in key.split("/"))


class Throttle:
    """At most `limit` failed sign-ins per client address in `window` seconds."""

    MAX_KEYS = 5000

    def __init__(self, limit: int = 20, window: int = 300):
        self.limit, self.window, self.fails, self.lock = limit, window, {}, threading.Lock()

    def blocked(self, who: str) -> bool:
        now = time.time()
        with self.lock:
            hits = [t for t in self.fails.get(who, []) if now - t < self.window]
            if hits:
                self.fails[who] = hits
            else:
                self.fails.pop(who, None)                   # no entry for an address that is not failing
            return len(hits) >= self.limit

    def fail(self, who: str) -> None:
        now = time.time()
        with self.lock:
            if len(self.fails) >= self.MAX_KEYS:            # bounded: drop addresses whose failures have all aged out
                for k in [k for k, v in self.fails.items() if not v or now - v[-1] >= self.window]:
                    del self.fails[k]
            self.fails.setdefault(who, []).append(now)


class Service:
    def __init__(self, data_dir: str, bucket: str | None = None):
        self.data = data_dir
        self.tokens_path = os.path.join(data_dir, "tokens.json")
        self.store = store_mod.Store(data_dir, bucket)
        backup.pull(self.store, self.tokens_path)             # a new VM with an empty disk gets its tokens back
        self.index = index_mod.Index(os.path.join(data_dir, "index.db"))
        self.spool = os.path.join(data_dir, "spool")
        self.throttle = Throttle()
        os.makedirs(self.spool, exist_ok=True)

    def tokens_changed(self) -> None:
        backup.push(self.store, self.tokens_path)

    def admin_overview(self) -> dict:
        """Everything the admin page shows: PC tokens with their state, the splits, and who can read."""
        data = tokens.load(self.tokens_path)
        runs = {}
        for r in self.index.runs():
            cur = runs.get(r["pc"])
            if cur is None or r["updated"] > cur["updated"]:
                runs[r["pc"]] = r
        now = time.time()
        pcs = []
        for name, rec in sorted(data["pcs"].items()):
            r = runs.get(name)
            reason = (r or {}).get("status", {}).get("reason")
            if rec.get("expires") and rec["expires"] < now:
                state = "expired"
            elif not r:
                state = "never"
            elif reason == "batch done":
                state = "done"
            elif reason == "stopped":
                state = "stopped"
            else:
                state = "mining" if now - r["updated"] < 25 * 60 else "silent"
            st = (r or {}).get("status", {})
            pcs.append({"pc": name, "split": rec.get("split"), "created": rec.get("created"), "expires": rec.get("expires"),
                        "last_upload": r["updated"] if r else None, "state": state, "host": st.get("host"),
                        "repos_done": st.get("repos_done"), "repos_total": st.get("repos_total"), "prs_done": st.get("prs_done")})
        splits = []
        for sid, meta in sorted(data["splits"].items()):
            mine = [p for p in pcs if p["split"] == sid]
            shown = {k: v for k, v in meta.items() if k != "repos_text"}
            splits.append(dict(shown, split_id=sid, tokens=len(mine), uploaded=sum(1 for p in mine if p["last_upload"]),
                               can_rotate=bool(meta.get("flags") is not None),
                               n_repos=sum(1 for ln in (meta.get("repos_text") or "").splitlines() if ln.strip() and not ln.strip().startswith("#"))))
        names = lambda kind: [{"name": n, "created": r.get("created")} for n, r in sorted(data[kind].items())]   # noqa: E731
        return {"pcs": pcs, "splits": splits, "readers": names("readers"), "admins": names("admins"),
                "bucket": self.store.bucket_name,
                "backup": dict(backup.STATE) if self.store.bucket_name else None}

    # -- uploads ---------------------------------------------------------
    def put(self, pc: str, key: str, rfile, length: int, want_sha: str | None) -> tuple[int, str]:
        if not valid_key(key):
            return 400, "bad key"
        if length < 0:
            return 400, "bad Content-Length"
        if length > MAX_FILE:
            return 413, "too large"
        full = f"{pc}/{key}"
        fd, tmp = tempfile.mkstemp(dir=self.spool)
        h, left = hashlib.sha256(), length
        try:
            with os.fdopen(fd, "wb") as out:
                while left:
                    block = rfile.read(min(1024 * 1024, left))
                    if not block:
                        break
                    out.write(block)
                    h.update(block)
                    left -= len(block)
            if left:
                os.remove(tmp)
                return 400, "body shorter than Content-Length"
            if want_sha and want_sha.lower() != h.hexdigest():
                os.remove(tmp)
                return 422, "sha256 mismatch"
            status = None
            if key.endswith("/run.json"):
                try:
                    if length > MAX_RUN_JSON:
                        raise ValueError("too large")
                    with open(tmp, encoding="utf-8") as fh:
                        status = json.load(fh)
                    if not isinstance(status, dict):
                        raise ValueError("not an object")
                except (OSError, ValueError):
                    os.remove(tmp)
                    return 400, "run.json must be a JSON object (at most 1 MB)"
            self.store.put(full, tmp)
        except Exception:                                   # noqa: BLE001
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
        self.index.put_file(full, pc, length, h.hexdigest())
        if status is not None:
            self.index.put_run(pc, key[: -len("/run.json")], status)
        return 200, "ok"

    # -- reading ---------------------------------------------------------
    def search(self, q: str, pc: str, regex: bool) -> tuple[list[dict], str]:
        if regex and (len(q) > 200 or NESTED_QUANTIFIER.search(q)):
            return [], "bad regex: that pattern can take for ever on a long line (a repeat inside a repeat); simplify it"
        try:
            pat = re.compile(q if regex else re.escape(q), re.I)
        except re.error as e:
            return [], f"bad regex: {e}"
        hits, scanned, note = [], 0, ""
        deadline = time.monotonic() + SEARCH_BUDGET_S
        for f in self.index.files(pc or None, "%/logs/%"):
            fh = self.store.open_local(f["key"])
            if fh is None:
                continue
            scanned += f["size"]
            if scanned > SEARCH_MAX_BYTES:
                fh.close()
                note = "Search stopped at its size limit; narrow it to one PC."
                break
            with fh:
                for n, raw in enumerate(fh, 1):
                    if time.monotonic() > deadline:
                        return hits, "Search stopped at its time limit; narrow it to one PC or a simpler pattern."
                    line = raw[:SEARCH_LINE * 4].decode("utf-8", "replace").rstrip()
                    if pat.search(line[:SEARCH_LINE]):
                        hits.append({"key": f["key"], "line": n, "text": line[:400]})
                        if len(hits) >= SEARCH_MAX_HITS:
                            return hits, f"Showing the first {SEARCH_MAX_HITS} matches."
        return hits, note

    def health_for(self, pc: str, prefix: str) -> dict[str, dict]:
        out = {}
        for f in self.index.files(pc, f"{pc}/{prefix}/health/%"):
            fh = self.store.open_local(f["key"])
            if fh is None:
                continue
            with fh:
                try:
                    rec = json.load(fh)
                    out[rec["repo"]] = rec
                except (ValueError, KeyError, TypeError):
                    continue
        return out

    def run_detail(self, pc: str, prefix: str) -> dict | None:
        run = self.index.run(pc, prefix)
        if not run:
            return None
        files = self.index.files(pc, f"{pc}/{prefix}/%")
        for f in files:
            f["kind"] = views.kind_of(f["key"])
            f["uri"] = self.store.uri(f["key"])
        return {"run": run, "files": files, "health": self.health_for(pc, prefix)}

    def table_catalogue(self, version: str | None, split: str | None) -> dict:
        data = [f["key"] for f in self.index.files(like="%/data/%.parquet")]
        combos = sorted({(k.split("/")[1], k.split("/")[2]) for k in data if k.count("/") == 4})
        if not combos:
            return {"versions": [], "splits": [], "tables": [], "version": None, "split": None}
        if (version, split) not in combos:
            version, split = combos[-1]
        finished = {r["pc"] for r in self.index.runs()
                    if r["prefix"] == f"{version}/{split}" and isinstance(r["status"], dict) and r["status"].get("reason") == "batch done"}
        out = []
        for incl in (False, True):
            files, _notes = tables.plan(data, version, split, None if incl else finished)
            out.append(files)
        return {"versions": sorted({c[0] for c in combos}), "splits": sorted({c[1] for c in combos if c[0] == version}),
                "version": version, "split": split, "finished_pcs": sorted(finished),
                "tables": [{"name": t, "finished": len(out[0][t]), "all": len(out[1][t])} for t, _p in tables.TABLES]}

    def parquet(self, q: dict) -> views.Parquet:
        """The relation a request names: one file (`key`) or one table across PCs."""
        if q.get("key"):
            keys = [q["key"]]
        else:
            if q.get("table") not in {t for t, _p in tables.TABLES} or not WORD.match(q.get("version", "")) \
                    or not WORD.match(q.get("split", "")):
                raise ValueError("name a file (key) or a table with its version and split")
            cat = self.table_catalogue(q["version"], q["split"])
            data = [f["key"] for f in self.index.files(like="%/data/%.parquet")]
            finished = None if q.get("all") == "1" else set(cat.get("finished_pcs", []))
            keys = tables.plan(data, q["version"], q["split"], finished)[0][q["table"]]
            if not keys:
                raise LookupError("no finished PC has this table yet; tick 'include unfinished PCs'")
        paths = []
        for k in keys:
            if not valid_key(k) or not k.endswith(".parquet") or not self.index.file(k):
                raise LookupError("no such file")
            p = self.store.fetch(k)
            if not p:
                raise LookupError("file is not available")
            paths.append(p)
        return views.Parquet(paths, [self.store.root, self.store.cache])

    def text_path(self, key: str) -> str:
        if not valid_key(key) or not self.index.file(key):
            raise LookupError("no such file")
        p = self.store.fetch(key)
        if not p:
            raise LookupError("file is not available")
        return p


def _int(q: dict, name: str, default: int) -> int:
    try:
        return int(q.get(name, default))
    except ValueError:
        return default


def make_handler(svc: Service):
    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "riffle-data"
        protocol_version = "HTTP/1.1"
        timeout = SOCKET_TIMEOUT_S                          # an idle or stalled client must not hold a thread for ever

        def log_message(self, fmt, *a):
            sys.stderr.write("%s %s\n" % (self.client(), fmt % a))

        # -- plumbing -----------------------------------------------------
        def client(self) -> str:
            if TRUST_PROXY:
                fwd = self.headers.get("X-Forwarded-For", "")
                if fwd:                                     # the proxy appends the real peer last; earlier entries are the client's to forge
                    return fwd.split(",")[-1].strip()
            return self.client_address[0]

        def https(self) -> bool:
            return os.environ.get("RIFFLE_COOKIE_SECURE") == "1" or (
                TRUST_PROXY and self.headers.get("X-Forwarded-Proto") == "https")

        def _send(self, code: int, body: bytes | str = b"", ctype: str = "text/plain; charset=utf-8", extra=None):
            data = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", CSP)
            if "Cache-Control" not in (extra or {}):
                self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def _json(self, obj, code: int = 200):
            self._send(code, json.dumps(obj, default=str), "application/json")

        def _token(self) -> str | None:
            auth = self.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                return auth[7:].strip()
            if auth.startswith("Basic "):
                try:
                    return base64.b64decode(auth[6:]).decode().partition(":")[2]
                except (ValueError, UnicodeDecodeError):
                    return None
            cookie = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
            return cookie[COOKIE].value if COOKIE in cookie else None

        def _admin_token(self) -> str | None:
            """A Bearer admin token (scripts, the miner) or the admin page's own cookie."""
            if self.headers.get("Authorization"):
                return self._token()
            cookie = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
            return cookie[ADMIN_COOKIE].value if ADMIN_COOKIE in cookie else None

        def _public_url(self) -> str:
            return (os.environ.get("RIFFLE_PUBLIC_URL") or f"{'https' if self.https() else 'http'}://{self.headers.get('Host', 'localhost')}").rstrip("/")

        def _is_reader(self) -> bool:
            who = tokens.who(svc.tokens_path, self._token())
            return bool(who and who[0] == "readers")

        def _guard(self, page: bool) -> bool:
            """True when the reader is signed in; otherwise answers and returns False."""
            if svc.throttle.blocked(self.client()):
                self._send(429, "too many failed sign-ins; wait a few minutes")
                return False
            if self._is_reader():
                return True
            if self._token():
                svc.throttle.fail(self.client())
            if page:
                self._send(302, "", extra={"Location": "/login"})
            else:
                self._send(401, json.dumps({"error": "reader token required"}), "application/json",
                           {"WWW-Authenticate": 'Bearer realm="riffle-data"'})
            return False

        # -- PUT ------------------------------------------------------------
        def do_PUT(self):
            if svc.throttle.blocked(self.client()):
                return self._reject(429, "too many failed attempts")
            info = tokens.lookup(svc.tokens_path, self._token())
            if not info or info["kind"] != "pcs":
                svc.throttle.fail(self.client())
                return self._reject(401, "PC token required")
            who = (info["kind"], info["name"])
            path = urlparse(self.path).path
            if not path.startswith("/v1/files/"):
                return self._reject(404, "not found")
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                return self._reject(411, "Content-Length required")
            if length < 0:
                return self._reject(400, "bad Content-Length")
            key = unquote(path[len("/v1/files/"):])
            parts = key.split("/")
            if info["split"] and (len(parts) < 2 or parts[1] != info["split"]):
                return self._reject(403, f"this token is for split {info['split']}; this run's split is "
                                         f"{parts[1] if len(parts) > 1 else '?'}")
            code, msg = svc.put(who[1], key, self.rfile, length, self.headers.get("X-Content-SHA256"))
            if code != 200:
                self.close_connection = True                # the body may not have been read
            self._send(code, msg)

        def _reject(self, code: int, msg: str):
            self.close_connection = True                    # an unread body must not become the next request
            self._send(code, msg, extra={"Connection": "close"})

        # -- POST (sign in) ---------------------------------------------------
        def do_POST(self):
            path = urlparse(self.path).path
            cap = POST_CAP.get(path, POST_CAP_DEFAULT)
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = -1
            if length < 0 or length > cap:
                return self._reject(413 if length > cap else 400, "request body too large" if length > cap else "bad Content-Length")
            raw = self.rfile.read(length)
            if path.startswith("/v1/admin/"):
                return self._admin_post(path, raw)
            form = parse_qs(raw.decode("utf-8", "replace"))
            if path == "/admin/login":
                return self._admin_login(form)
            if path != "/login":
                return self._reject(404, "not found")
            if svc.throttle.blocked(self.client()):
                return self._send(429, ui.login("Too many failed attempts from this address. Wait a few minutes and try again.", True), "text/html; charset=utf-8")
            token = (form.get("token") or [""])[0].strip()
            who = tokens.who(svc.tokens_path, token)
            if not who or who[0] != "readers":
                svc.throttle.fail(self.client())
                return self._send(401, ui.login("That token was not accepted. Check that you copied all of it, and that it is a read token (it starts with rd_)."), "text/html; charset=utf-8")
            cookie = f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=2592000" + ("; Secure" if self.https() else "")
            self._send(302, "", extra={"Location": "/", "Set-Cookie": cookie})

        def _admin_login(self, form: dict):
            html_t = "text/html; charset=utf-8"
            if svc.throttle.blocked(self.client()):
                return self._send(429, ui.login("Too many failed attempts from this address. Wait a few minutes and try again.", True, admin=True), html_t)
            token = (form.get("token") or [""])[0].strip()
            who = tokens.who(svc.tokens_path, token)
            if not who or who[0] != "admins":
                svc.throttle.fail(self.client())
                return self._send(401, ui.login("That token was not accepted. The admin page needs an admin token (it starts with ad_), "
                                                "not a read token.", admin=True), html_t)
            cookie = f"{ADMIN_COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=28800" + ("; Secure" if self.https() else "")
            self._send(302, "", extra={"Location": "/admin", "Set-Cookie": cookie})

        # -- admin: token issue and revoke ------------------------------------
        def _admin(self) -> bool:
            if svc.throttle.blocked(self.client()):
                self._send(429, json.dumps({"error": "too many failed attempts"}), "application/json")
                return False
            who = tokens.who(svc.tokens_path, self._admin_token())
            if who and who[0] == "admins":
                return True
            if self._admin_token():
                svc.throttle.fail(self.client())
            self._send(401, json.dumps({"error": "admin token required"}), "application/json")
            return False

        def _admin_post(self, path: str, raw: bytes):
            if (not self.headers.get("Authorization") and self._admin_token()
                    and self.headers.get("X-Requested-With") != "riffle"):               # a cookie call must come from our own page
                return self._send(403, json.dumps({"error": "missing X-Requested-With"}), "application/json")
            if not self._admin():
                return
            try:
                body = json.loads(raw or b"{}")
                if path == "/v1/admin/splits":
                    flags = body.get("flags")
                    out = tokens.issue_split(svc.tokens_path, str(body.get("split_id", "")), int(body.get("n", 0)),
                                             float(body["days"]) if body.get("days") else None,
                                             flags if isinstance(flags, list) else None, body.get("miner_version"),
                                             body.get("repos_name"), body.get("repos_text"),
                                             (tokens.who(svc.tokens_path, self._admin_token()) or ("", ""))[1])
                    svc.tokens_changed()
                    return self._json({"split_id": body["split_id"], "pcs": out})
                if path == "/v1/admin/rotate":
                    sid = str(body.get("split_id", ""))
                    meta = tokens.load(svc.tokens_path)["splits"].get(sid)
                    if not meta:
                        return self._json({"error": f"no split {sid}"}, 404)
                    if not os.environ.get("RIFFLE_PUBLIC_URL") and re.match(r"^(localhost|127\.|\[?::1)", self.headers.get("Host", "localhost")):
                        # the old tokens would be revoked and the new codes would point at an address no other PC can reach
                        return self._json({"error": "set RIFFLE_PUBLIC_URL (the address PCs use) before rotating, or open "
                                                    "the admin page through that address, not through localhost"}, 409)
                    days = float(body["days"]) if body.get("days") not in (None, "") else meta.get("days")
                    out = tokens.issue_split(svc.tokens_path, sid, int(meta["n"]), days or None, meta.get("flags", []), meta.get("version"))
                    # the repos list, name and issuer carry over: issue_split keeps what it is not given
                    svc.tokens_changed()
                    url = self._public_url()
                    for p in out:
                        p["code"] = join_code(url, p.pop("token"), sid, p["k"], p["n"], meta.get("version"), meta.get("flags", []))
                    return self._json({"split_id": sid, "pcs": out})
                if path == "/v1/admin/revoke":
                    if body.get("split_id"):
                        gone = tokens.revoke_split(svc.tokens_path, str(body["split_id"]))
                    else:
                        kind, name = str(body.get("kind") or "pcs"), str(body.get("name", ""))   # a bare name means a PC token
                        if kind == "admins":
                            me = tokens.who(svc.tokens_path, self._admin_token())
                            admins = tokens.load(svc.tokens_path)["admins"]
                            if (me and me[1] == name) or (name in admins and len(admins) <= 1):
                                return self._json({"error": "an admin cannot revoke itself or the last admin; add another admin first"}, 409)
                        gone = [name] if tokens.revoke_kind(svc.tokens_path, kind, name) else []
                    if gone:
                        svc.tokens_changed()
                    return self._json({"revoked": gone})
                if path == "/v1/admin/readers":
                    name = str(body.get("name", ""))
                    token = tokens.issue(svc.tokens_path, "readers", name)
                    svc.tokens_changed()
                    return self._json({"name": name, "token": token})
            except (ValueError, KeyError, TypeError) as e:
                return self._json({"error": str(e) or "bad request"}, 400)
            self._json({"error": "unknown endpoint"}, 404)

        def _split_info(self):
            """What a joined PC may read about its own split: the lead's flags, repos list and who issued it."""
            info = tokens.lookup(svc.tokens_path, self._token())
            if not info or info["kind"] != "pcs":
                svc.throttle.fail(self.client())
                return self._json({"error": "PC token required"}, 401)
            meta = tokens.load(svc.tokens_path)["splits"].get(info["split"] or "")
            if not meta:
                return self._json({"error": "this token is not for a split the store knows"}, 404)
            n_repos = sum(1 for ln in (meta.get("repos_text") or "").splitlines() if ln.strip() and not ln.strip().startswith("#"))
            return self._json({"split_id": info["split"], "pc": info["name"], "n": meta["n"], "flags": meta.get("flags", []),
                               "version": meta.get("version"), "created": meta.get("created"), "days": meta.get("days"),
                               "expires": info.get("expires"), "issued_by": meta.get("issued_by"),
                               "repos_name": meta.get("repos_name"), "repos_text": meta.get("repos_text"), "n_repos": n_repos})

        def _admin_overview(self):
            if self._admin():
                self._json(svc.admin_overview())

        def _admin_pcs(self):
            if not self._admin():
                return
            last = {}
            for r in svc.index.runs():
                last[r["pc"]] = max(last.get(r["pc"], 0), r["updated"])
            rows = [{"pc": n, "split": r.get("split"), "created": r.get("created"), "expires": r.get("expires"),
                     "last_upload": last.get(n)} for n, r in sorted(tokens.load(svc.tokens_path)["pcs"].items())]
            self._json({"pcs": rows})

        # -- GET ------------------------------------------------------------
        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            path = url.path
            html_t = "text/html; charset=utf-8"
            if path == "/healthz":
                return self._send(200, "ok")
            if path == "/v1/whoami":
                info = tokens.lookup(svc.tokens_path, self._token())
                if not info:
                    svc.throttle.fail(self.client())
                    return self._json({"error": "token not accepted (wrong, revoked or expired)"}, 401)
                return self._json(info)
            if path == "/v1/split":
                return self._split_info()
            if path == "/v1/admin/pcs":
                return self._admin_pcs()
            if path == "/v1/admin/overview":
                return self._admin_overview()
            if path == "/admin/login":
                return self._send(200, ui.login(admin=True), html_t)
            if path == "/admin/logout":
                return self._send(302, "", extra={"Location": "/admin/login", "Set-Cookie": f"{ADMIN_COOKIE}=; Path=/; Max-Age=0"})
            if path == "/admin":
                who = tokens.who(svc.tokens_path, self._admin_token())
                if not who or who[0] != "admins":
                    return self._send(302, "", extra={"Location": "/admin/login"})
                return self._send(200, ui.shell("admin", "Admin"), html_t)
            if path.startswith("/static/"):
                return self._static(path[len("/static/"):])
            if path == "/login":
                return self._send(200, ui.login(), html_t)
            if path == "/logout":
                return self._send(302, "", extra={"Location": "/login", "Set-Cookie": f"{COOKIE}=; Path=/; Max-Age=0"})
            pages = {"/": "overview", "/run": "run", "/data": "data", "/view": "view", "/search": "search"}
            if path in pages:
                if not self._guard(page=True):
                    return
                return self._send(200, ui.shell(pages[path], pages[path].title()), html_t)
            if path.startswith("/api/"):
                if not self._guard(page=False):
                    return
                try:
                    return self._api(path[len("/api/"):], q)
                except LookupError as e:
                    return self._json({"error": str(e)}, 404)
                except ValueError as e:
                    return self._json({"error": str(e)}, 400)
            self._send(404, "not found")

        def _static(self, name: str):
            if not re.match(r"^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)?$", name) or ".." in name:
                return self._send(404, "not found")
            full = os.path.join(STATIC, *name.split("/"))
            if not os.path.isfile(full):
                return self._send(404, "not found")
            with open(full, "rb") as fh:
                body = fh.read()
            ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
            if full.endswith(".woff2"):
                ctype = "font/woff2"
            self._send(200, body, ctype + ("; charset=utf-8" if ctype.startswith("text/") or ctype.endswith("javascript") else ""),
                       {"Cache-Control": "public, max-age=3600"})

        def _api(self, route: str, q: dict):
            if route == "runs":
                return self._json(svc.index.runs())
            if route == "run":
                d = svc.run_detail(q.get("pc", ""), q.get("prefix", ""))
                if not d:
                    raise LookupError("no such run")
                return self._json(d)
            if route == "tables":
                return self._json(svc.table_catalogue(q.get("version"), q.get("split")))
            if route == "search":
                hits, note = svc.search(q.get("q", ""), q.get("pc", ""), q.get("regex") == "1") if q.get("q") else ([], "")
                return self._json({"hits": hits, "note": note, "pcs": sorted({r["pc"] for r in svc.index.runs()})})
            if route.startswith("parquet/"):
                p = svc.parquet(q)
                try:
                    op = route.split("/", 1)[1]
                    if op == "schema":
                        return self._json(p.schema())
                    if op == "rows":
                        return self._json(p.rows(_int(q, "offset", 0), _int(q, "limit", 50), q.get("sort"),
                                                 q.get("dir", "asc"), q.get("fcol"), q.get("fq")))
                    if op == "profile":
                        return self._json(p.profile(q.get("col", "")))
                    if op == "charts":
                        return self._json(p.charts())
                finally:
                    p.close()
                raise LookupError("unknown parquet operation")
            if route in ("meta", "lines", "jsonl", "json", "trace"):
                key = q.get("key", "")
                path = svc.text_path(key)
                if route == "meta":
                    f = svc.index.file(key)
                    return self._json({"key": key, "kind": views.kind_of(key), "size": f["size"], "updated": f["updated"],
                                       "uri": svc.store.uri(key), "sha256": f["sha256"]})
                if route == "lines":
                    return self._json(views.lines(path, _int(q, "offset", 0), _int(q, "limit", 200), q.get("q"), q.get("tail") == "1", q.get("lvl") == "1"))
                if route == "jsonl":
                    return self._json(views.jsonl_rows(path, _int(q, "offset", 0), _int(q, "limit", 50), q.get("q")))
                if route == "json":
                    return self._json(views.json_doc(path))
                return self._json(views.trace_summary(path))
            raise LookupError("unknown endpoint")

    return Handler


def serve(data_dir: str, host: str, port: int, bucket: str | None = None) -> http.server.ThreadingHTTPServer:
    svc = Service(data_dir, bucket)
    srv = http.server.ThreadingHTTPServer((host, port), make_handler(svc))
    srv.daemon_threads = True
    srv.service = svc                                       # type: ignore[attr-defined]
    return srv


if __name__ == "__main__":
    data = os.environ.get("RIFFLE_DATA", "/data")
    server = serve(data, os.environ.get("RIFFLE_HOST", "0.0.0.0"), int(os.environ.get("PORT", "8470")),
                   os.environ.get("RIFFLE_BUCKET") or None)
    print(f"riffle-data on :{server.server_port}, data {data}, bucket {os.environ.get('RIFFLE_BUCKET') or 'none'}",
          flush=True)
    server.serve_forever()
