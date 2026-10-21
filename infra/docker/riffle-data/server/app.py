"""
app.py — the team store's HTTP service (ADR 0014). Standard library only,
apart from google-cloud-storage when RIFFLE_BUCKET is set.

  PUT  /v1/files/<key>            a PC's token; stored as <pc>/<key>
  GET  /healthz                   open
  GET  /                          overview page            (reader token)
  GET  /run?pc=&prefix=           one PC's repos and files (reader token)
  GET  /search?q=&pc=&regex=1     grep over the run logs   (reader token)
  GET  /file/<pc>/<key>[?tail=N]  a log, health file or run.json
  GET  /api/runs                  the same data as JSON

Readers send `Authorization: Bearer <token>` or, in a browser, any username
with the token as the password. A PC's token can write only; a reader's can
only read. Nothing here is on the scoring path.
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import re
import sys
import tempfile
from urllib.parse import parse_qs, unquote, urlparse

import index as index_mod
import store as store_mod
import tokens
import ui

KEY = re.compile(r"^[A-Za-z0-9._-]{1,120}(/[A-Za-z0-9._-]{1,120}){1,7}$")
MAX_FILE = int(os.environ.get("RIFFLE_MAX_FILE_BYTES", 2 * 1024 ** 3))
SEARCH_MAX_HITS = 300
SEARCH_MAX_BYTES = 400 * 1024 ** 2


def valid_key(key: str) -> bool:
    return bool(KEY.match(key)) and len(key) <= 400 and all(p not in (".", "..") for p in key.split("/"))


class Service:
    def __init__(self, data_dir: str, bucket: str | None = None):
        self.data = data_dir
        self.tokens_path = os.path.join(data_dir, "tokens.json")
        self.store = store_mod.Store(data_dir, bucket)
        self.index = index_mod.Index(os.path.join(data_dir, "index.db"))
        self.spool = os.path.join(data_dir, "spool")
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

    # -- search ----------------------------------------------------------
    def search(self, q: str, pc: str, regex: bool) -> tuple[list[tuple[str, int, str]], str]:
        try:
            pat = re.compile(q if regex else re.escape(q), re.I)
        except re.error as e:
            return [], f"bad regex: {e}"
        hits, scanned, note = [], 0, ""
        for f in self.index.files(pc or None, "%/logs/%.log"):
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
                        hits.append((f["key"], n, line[:400]))
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


def make_handler(svc: Service):
    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "riffle-data"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *a):
            sys.stderr.write("%s %s\n" % (self.address_string(), fmt % a))

        # -- plumbing -----------------------------------------------------
        def _send(self, code: int, body: bytes | str = b"", ctype: str = "text/plain; charset=utf-8", extra=None):
            data = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def _token(self) -> str | None:
            auth = self.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                return auth[7:].strip()
            if auth.startswith("Basic "):
                try:
                    return base64.b64decode(auth[6:]).decode().partition(":")[2]
                except (ValueError, UnicodeDecodeError):
                    return None
            return None

        def _reader(self) -> bool:
            who = tokens.who(svc.tokens_path, self._token())
            if who and who[0] == "readers":
                return True
            self._send(401, "reader token required", extra={"WWW-Authenticate": 'Basic realm="riffle-data"'})
            return False

        # -- PUT ------------------------------------------------------------
        def do_PUT(self):
            who = tokens.who(svc.tokens_path, self._token())
            if not who or who[0] != "pcs":
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
            # an unread body would be parsed as the next request on this connection
            self.close_connection = True
            self._send(code, msg, extra={"Connection": "close"})

        # -- GET ------------------------------------------------------------
        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            if url.path == "/healthz":
                return self._send(200, "ok")
            if not self._reader():
                return
            html_t = "text/html; charset=utf-8"
            if url.path == "/":
                return self._send(200, ui.overview(svc.index.runs()), html_t)
            if url.path == "/api/runs":
                return self._send(200, json.dumps(svc.index.runs()), "application/json")
            if url.path == "/run":
                run = svc.index.run(q.get("pc", ""), q.get("prefix", ""))
                if not run:
                    return self._send(404, "no such run")
                files = svc.index.files(run["pc"], f"{run['pc']}/{run['prefix']}/%")
                uris = {f["key"]: svc.store.uri(f["key"]) for f in files}
                return self._send(200, ui.run_page(run, svc.health_for(run["pc"], run["prefix"]), files, uris), html_t)
            if url.path == "/search":
                hits, note = (svc.search(q["q"], q.get("pc", ""), q.get("regex") == "1") if q.get("q") else ([], ""))
                pcs = sorted({r["pc"] for r in svc.index.runs()})
                return self._send(200, ui.search_page(q.get("q", ""), q.get("pc", ""), q.get("regex") == "1",
                                                      pcs, hits, note), html_t)
            if url.path.startswith("/file/"):
                key = unquote(url.path[len("/file/"):])
                if not valid_key(key) or not store_mod.is_mirrored(key):
                    return self._send(404, "not viewable here; big files are in the bucket")
                fh = svc.store.open_local(key)
                if fh is None:
                    return self._send(404, "no such file")
                with fh:
                    size = os.fstat(fh.fileno()).st_size
                    tail = int(q.get("tail") or 0) if (q.get("tail") or "").isdigit() else 0
                    if tail and size > tail:
                        fh.seek(size - tail)
                    return self._send(200, fh.read())
            self._send(404, "not found")

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
