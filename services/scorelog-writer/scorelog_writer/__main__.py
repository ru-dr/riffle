"""The score-log writer process: reads score-log, writes encrypted Parquet, health on 8080, metrics on 9090.

    python -m scorelog_writer               # run
    python -m scorelog_writer read <gs://bucket/score_log/...parquet.enc>   # decrypt one file to stdout (as CSV)

On SIGTERM it stops pulling, writes every held batch, then exits, so a rollout loses nothing; anything not
acknowledged is delivered again.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from prometheus_client import start_http_server

from . import keys as keys_mod
from .writer import Writer

RESERVED = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, r):
        o = {"time": self.formatTime(r, "%Y-%m-%dT%H:%M:%S"), "level": r.levelname.lower(), "logger": r.name,
             "msg": r.getMessage(), **{k: v for k, v in vars(r).items() if k not in RESERVED}}
        if r.exc_info:
            o["error"] = self.formatException(r.exc_info)
        return json.dumps(o, default=str)


def setup_logging():
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter())
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), handlers=[h], force=True)


def contracts_dir() -> Path:
    if os.environ.get("CONTRACTS_DIR"):
        return Path(os.environ["CONTRACTS_DIR"])
    here = Path(__file__).resolve()
    for p in (here.parents[1] / "contracts", here.parents[3] / "contracts"):    # image, then repo checkout
        if (p / "score_log.schema.json").is_file():
            return p
    raise FileNotFoundError("contracts/ not found; set CONTRACTS_DIR")


def validator():
    import jsonschema
    schema = json.loads((contracts_dir() / "score_log.schema.json").read_text(encoding="utf-8"))
    return jsonschema.Draft202012Validator(schema, format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER)


def conninfo() -> str:
    from psycopg.conninfo import make_conninfo
    e = os.environ
    return make_conninfo(host=e.get("DB_HOST", "127.0.0.1"), port=e.get("DB_PORT", "5432"), user=e["DB_USER"],
                         dbname=e["DB_NAME"], password=e.get("DB_PASSWORD") or None,
                         sslmode=e.get("DB_SSLMODE", "disable"))


def serve_health(port: int, ready: threading.Event, stopping: threading.Event):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            ok = {"/healthz": not stopping.is_set(),
                  "/readyz": ready.is_set() and not stopping.is_set()}.get(self.path)
            if ok is None:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200 if ok else 503)
            self.end_headers()
            self.wfile.write(b"ok\n" if ok else b"not ready\n")

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def run():
    setup_logging()
    log = logging.getLogger("scorelog_writer")
    env = os.environ
    from google.cloud import pubsub_v1, storage

    ready, stopping, stop = threading.Event(), threading.Event(), threading.Event()
    serve_health(int(env.get("HEALTH_PORT", "8080")), ready, stopping)
    start_http_server(int(env.get("METRICS_PORT", "9090")))

    key_store = keys_mod.KeyStore(keys_mod.Db(conninfo()), keys_mod.Kms(env["TENANT_KEK_ID"]),
                                  cache_seconds=float(env.get("KEY_CACHE_SECONDS", "600")))
    writer = Writer(key_store, storage.Client().bucket(env["BUCKET_SCORE_LOG"]), validator(),
                    batch_rows=int(env.get("BATCH_ROWS", "500")),
                    flush_seconds=float(env.get("FLUSH_SECONDS", "60")))

    def flusher():
        while not stop.wait(1.0):
            writer.flush()

    threading.Thread(target=flusher, daemon=True, name="flush").start()

    def callback(message):
        if stopping.is_set():
            message.nack()      # shutting down: another pod takes it
            return
        writer.add(message)

    subscriber = pubsub_v1.SubscriberClient()
    path = subscriber.subscription_path(env["PUBSUB_PROJECT_ID"], env["SUBSCRIPTION_SCORE_LOG_WRITER"])
    # Enough messages in hand to fill several files; the client keeps extending their deadlines while held.
    future = subscriber.subscribe(path, callback, flow_control=pubsub_v1.types.FlowControl(
        max_messages=int(env.get("MAX_HELD_MESSAGES", "5000")), max_lease_duration=600))
    ready.set()
    log.info("consuming", extra={"subscription": path})

    def shutdown(*_):
        stopping.set()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    while not stopping.wait(1.0):
        if future.done():
            log.error("subscriber stopped", exc_info=future.exception())
            break
    stop.set()
    written = writer.flush(everything=True)
    future.cancel()
    try:
        future.result(timeout=20)
    except Exception:  # noqa: BLE001  cancelled
        pass
    subscriber.close()
    log.info("stopped", extra={"files_written_at_shutdown": written})
    if not stopping.is_set():
        sys.exit(1)


def read(url: str):
    """Decrypt one score log file and print it as CSV, for debugging. Needs Postgres and KMS access."""
    import io

    import pyarrow.csv as pcsv
    import pyarrow.parquet as pq
    from google.cloud import storage

    from . import crypto
    bucket, name = url.removeprefix("gs://").split("/", 1)
    tenant = name.split("tenant=", 1)[1].split("/", 1)[0]
    key = keys_mod.KeyStore(keys_mod.Db(conninfo()), keys_mod.Kms(os.environ["TENANT_KEK_ID"])).data_key(tenant)
    if key is None:
        raise SystemExit(f"tenant {tenant} has no data key")
    blob = storage.Client().bucket(bucket).blob(name).download_as_bytes()
    out = io.BytesIO()
    pcsv.write_csv(pq.read_table(io.BytesIO(crypto.decrypt(blob, key, name))), out)
    sys.stdout.write(out.getvalue().decode())


if __name__ == "__main__":
    if sys.argv[1:2] == ["read"] and len(sys.argv) == 3:
        read(sys.argv[2])
    else:
        run()
