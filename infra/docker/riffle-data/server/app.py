"""
app.py — the team store's HTTP service (ADR 0015). Standard library only, apart
from google-cloud-storage (RIFFLE_BUCKET) and duckdb (the parquet viewer).

  PUT  /v1/files/<key>      a PC's token; stored as <pc>/<key>
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
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
COOKIE = "riffle_session"


def valid_key(key: str) -> bool:
    return bool(KEY.match(key)) and len(key) <= 400 and all(p not in (".", "..") for p in key.split("/"))


class Throttle:
    """At most `limit` failed sign-ins per client address in `window` seconds."""

    def __init__(self, limit: int = 20, window: int = 300):
        self.limit, self.window, self.fails, self.lock = limit, window, {}, threading.Lock()

    def blocked(self, who: str) -> bool:
        now = time.time()
        with self.lock:
            hits = [t for t in self.fails.get(who, []) if now - t < self.window]
            self.fails[who] = hits
            return len(hits) >= self.limit

    def fail(self, who: str) -> None:
        with self.lock:
            self.fails.setdefault(who, []).append(time.time())


class Service:
    def __init__(self, data_dir: str, bucket: str | None = None):
        self.data = data_dir
        self.tokens_path = os.path.join(data_dir, "tokens.json")
        self.store = store_mod.Store(data_dir, bucket)
        self.index = index_mod.Index(os.path.join(data_dir, "index.db"))
        self.spool = os.path.join(data_dir, "spool")
        self.throttle = Throttle()
        os.makedirs(self.spool, exist_ok=True)

    # -- uploads ---------------------------------------------------------
    def put(self, pc: str, key: str, rfile, length: int, want_sha: str | None) -> tuple[int, str]:
        if not valid_key(key):
            return 400, "bad key"
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
                    with open(tmp, encoding="utf-8") as fh:
                        status = json.load(fh)
                except (OSError, ValueError):
                    os.remove(tmp)
                    return 400, "run.json is not JSON"
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
        try:
            pat = re.compile(q if regex else re.escape(q), re.I)
        except re.error as e:
            return [], f"bad regex: {e}"
        hits, scanned, note = [], 0, ""
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
                    line = raw.decode("utf-8", "replace").rstrip()
                    if pat.search(line):
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
                    if r["prefix"] == f"{version}/{split}" and r["status"].get("reason") == "batch done"}
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

        def log_message(self, fmt, *a):
            sys.stderr.write("%s %s\n" % (self.client(), fmt % a))

        # -- plumbing -----------------------------------------------------
        def client(self) -> str:
            if TRUST_PROXY:
                fwd = self.headers.get("X-Forwarded-For", "")
                if fwd:
                    return fwd.split(",")[0].strip()
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
            who = tokens.who(svc.tokens_path, self._token())
            if not who or who[0] != "pcs":
                svc.throttle.fail(self.client())
                return self._reject(401, "PC token required")
            path = urlparse(self.path).path
            if not path.startswith("/v1/files/"):
                return self._reject(404, "not found")
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                return self._reject(411, "Content-Length required")
            code, msg = svc.put(who[1], unquote(path[len("/v1/files/"):]), self.rfile, length,
                                self.headers.get("X-Content-SHA256"))
            if code != 200:
                self.close_connection = True                # the body may not have been read
            self._send(code, msg)

        def _reject(self, code: int, msg: str):
            self.close_connection = True                    # an unread body must not become the next request
            self._send(code, msg, extra={"Connection": "close"})

        # -- POST (sign in) ---------------------------------------------------
        def do_POST(self):
            path = urlparse(self.path).path
            try:
                length = min(int(self.headers.get("Content-Length", "0")), 4096)
            except ValueError:
                length = 0
            form = parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
            if path != "/login":
                return self._reject(404, "not found")
            if svc.throttle.blocked(self.client()):
                return self._send(429, ui.login("Too many failed sign-ins. Wait a few minutes."), "text/html; charset=utf-8")
            token = (form.get("token") or [""])[0].strip()
            who = tokens.who(svc.tokens_path, token)
            if not who or who[0] != "readers":
                svc.throttle.fail(self.client())
                return self._send(401, ui.login("That token is not a read token."), "text/html; charset=utf-8")
            cookie = f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=2592000" + ("; Secure" if self.https() else "")
            self._send(302, "", extra={"Location": "/", "Set-Cookie": cookie})

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
                    return self._json(views.lines(path, _int(q, "offset", 0), _int(q, "limit", 200), q.get("q"), q.get("tail") == "1"))
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
