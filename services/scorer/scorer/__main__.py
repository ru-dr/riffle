"""The scorer process: model checks, the Pub/Sub consumer, health on 8080 and metrics on 9090.

    python -m scorer               # run
    python -m scorer healthcheck   # exit 0 if /healthz answers (for a local docker run)

Fair shares: at most TENANT_MAX_IN_FLIGHT messages of one tenant are scored at once out of WORKERS; a tenant over
its share has the message nacked and redelivered after the subscription's backoff, so one busy installation cannot
hold every worker.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
import time
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from prometheus_client import Counter, Gauge, Histogram, start_http_server

from . import history, models, scoring, store

MESSAGES = Counter("riffle_scorer_messages_total", "PrFeatures messages handled", ["tenant", "result"])
LATENCY = Histogram("riffle_scorer_latency_seconds", "Time to handle one message", ["result"],
                    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30))
MODEL = Gauge("riffle_scorer_model_info", "The model in service (1)", ["model_version", "miner_version"])
SKIPPED_FAIR_SHARE = Counter("riffle_scorer_fair_share_deferred_total", "Messages deferred: tenant over its share",
                             ["tenant"])

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


class Health:
    def __init__(self, model_store):
        self.model_store, self.consuming, self.stopping = model_store, threading.Event(), threading.Event()

    def serve(self, port: int):
        health = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/healthz":
                    ok = not health.stopping.is_set()
                elif self.path == "/readyz":     # the first model check is done and messages are being pulled
                    ok = health.model_store.attempted.is_set() and health.consuming.is_set() \
                        and not health.stopping.is_set()
                else:
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


class Publisher:
    """Publishes to the topics named in riffle-platform-settings (TOPIC_SCORE_RESULTS, TOPIC_SCORE_LOG)."""

    def __init__(self, project: str, topics: dict[str, str]):
        from google.cloud import pubsub_v1
        self.client = pubsub_v1.PublisherClient()
        self.paths = {k: self.client.topic_path(project, v) for k, v in topics.items()}

    def publish(self, topic: str, message: dict, attributes: dict):
        return self.client.publish(self.paths[topic], json.dumps(message).encode(), **attributes)


class FairShare:
    def __init__(self, per_tenant: int):
        self.per_tenant, self.lock, self.busy = per_tenant, threading.Lock(), defaultdict(int)

    def try_acquire(self, tenant: str) -> bool:
        with self.lock:
            if self.busy[tenant] >= self.per_tenant:
                return False
            self.busy[tenant] += 1
            return True

    def release(self, tenant: str):
        with self.lock:
            self.busy[tenant] -= 1
            if self.busy[tenant] <= 0:
                del self.busy[tenant]


def make_callback(scorer: scoring.Scorer, fair: FairShare):
    log = logging.getLogger("scorer")

    def callback(message):
        tenant = message.attributes.get("tenant_id", "unknown")
        if not fair.try_acquire(tenant):
            SKIPPED_FAIR_SHARE.labels(tenant).inc()
            message.nack()
            return
        t0, result = time.perf_counter(), "error"
        try:
            result = scorer.handle(message.data, message.delivery_attempt)
            message.ack()
        except scoring.Retry:
            result = "retry"
            message.nack()
        except Exception:  # noqa: BLE001  store or Pub/Sub down: not done, redeliver
            log.exception("not stored or not published: redelivering",
                          extra={"delivery_id": message.attributes.get("delivery_id"), "tenant_id": tenant})
            message.nack()
        finally:
            fair.release(tenant)
            MESSAGES.labels(tenant, result).inc()
            LATENCY.labels(result).observe(time.perf_counter() - t0)

    return callback


def run():
    setup_logging()
    log = logging.getLogger("scorer")
    env = os.environ
    workers = int(env.get("WORKERS", "16"))
    stop = threading.Event()

    model_store = models.from_env()
    health = Health(model_store)
    health.serve(int(env.get("HEALTH_PORT", "8080")))
    start_http_server(int(env.get("METRICS_PORT", "9090")))

    def watch_models():
        shown = None
        while not stop.is_set():
            model_store.refresh()
            cur = model_store.current
            if cur and cur.model_version != shown:
                MODEL.clear()
                MODEL.labels(cur.model_version, cur.miner_version or "").set(1)
                shown = cur.model_version
            stop.wait(float(env.get("MODEL_CHECK_SECONDS", "60")))

    threading.Thread(target=watch_models, daemon=True, name="models").start()
    model_store.attempted.wait(timeout=float(env.get("MODEL_FIRST_WAIT_SECONDS", "300")))

    project = env["PUBSUB_PROJECT_ID"]
    scorer = scoring.Scorer(
        models=model_store, history=history.from_env(), store=store.from_env(),
        publisher=Publisher(project, {"score-results": env["TOPIC_SCORE_RESULTS"],
                                      "score-log": env["TOPIC_SCORE_LOG"]}),
        contracts=scoring.Contracts())

    from google.cloud import pubsub_v1
    from google.cloud.pubsub_v1.subscriber.scheduler import ThreadScheduler
    subscriber = pubsub_v1.SubscriberClient()
    path = subscriber.subscription_path(project, env["SUBSCRIPTION_SCORER_PR_FEATURES"])
    future = subscriber.subscribe(
        path, make_callback(scorer, FairShare(int(env.get("TENANT_MAX_IN_FLIGHT", str(max(1, workers // 4)))))),
        flow_control=pubsub_v1.types.FlowControl(max_messages=workers),
        scheduler=ThreadScheduler(ThreadPoolExecutor(max_workers=workers, thread_name_prefix="score")))
    health.consuming.set()
    log.info("consuming", extra={"subscription": path, "workers": workers})

    def shutdown(*_):
        health.stopping.set()
        stop.set()
        future.cancel()     # stop pulling; messages in hand are finished, unacked ones are redelivered

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        future.result()
    except Exception:  # noqa: BLE001  cancelled on shutdown, or the stream failed
        if not stop.is_set():
            log.exception("subscriber stopped")
            sys.exit(1)
    finally:
        subscriber.close()
    log.info("stopped")


def healthcheck() -> int:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{os.environ.get('HEALTH_PORT', '8080')}/healthz", timeout=3):
            return 0
    except Exception:  # noqa: BLE001
        return 1


if __name__ == "__main__":
    if sys.argv[1:] == ["healthcheck"]:
        sys.exit(healthcheck())
    run()
