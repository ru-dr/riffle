"""score-log messages to encrypted Parquet files, one per tenant and day per flush (ADR 0024).

Messages are held, unacknowledged, in a buffer per (tenant, date) and written when a buffer reaches BATCH_ROWS
or its oldest message has waited FLUSH_SECONDS. A message is acknowledged only after its file is stored, so a
crash or a failed write loses nothing: Pub/Sub delivers the messages again. Rows the writer can never store are
acknowledged and counted:
  invalid      the message breaks contracts/score_log.schema.json
  duplicate    the same delivery_id twice in one file
  tenant_gone  the tenant is being deleted or does not exist (its data is being deleted)
If Postgres, KMS or the bucket fails, the batch is nacked and retried; after the subscription's
max_delivery_attempts the messages go to the dead letter.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field

from prometheus_client import Counter, Gauge, Histogram

from . import crypto, files

log = logging.getLogger("scorelog_writer")

ROWS = Counter("riffle_score_log_rows_total", "Score log rows handled (ADR 0024)", ["result"])
FILES = Counter("riffle_score_log_files_total", "Score log files written")
FAILURES = Counter("riffle_score_log_write_failures_total", "Batches not written (retried)")
BUFFERED = Gauge("riffle_score_log_buffered_rows", "Rows held, waiting for their file")
FLUSH = Histogram("riffle_score_log_flush_seconds", "Time to write one file",
                  buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30))


@dataclass
class Batch:
    tenant_id: str
    date: str
    started: float
    items: list = field(default_factory=list)       # (row, message)


class Writer:
    def __init__(self, keys, bucket, validator, batch_rows: int = 500, flush_seconds: float = 60.0,
                 now=time.monotonic):
        self.keys, self.bucket, self.validator = keys, bucket, validator
        self.batch_rows, self.flush_seconds, self.now = batch_rows, flush_seconds, now
        self._batches: dict[tuple[str, str], Batch] = {}
        self._lock = threading.Lock()

    def add(self, message) -> None:
        """Hold one Pub/Sub message; full batches are written at once, in the caller's thread."""
        try:
            row = json.loads(message.data)
            errors = [e.message for e in self.validator.iter_errors(row)] if isinstance(row, dict) else ["not an object"]
        except ValueError:
            errors = ["not JSON"]
        if errors:
            log.error("invalid score log row: %s", "; ".join(errors[:3]),
                      extra={"delivery_id": message.attributes.get("delivery_id")})
            ROWS.labels("invalid").inc()
            message.ack()
            return
        key = (row["tenant_id"], row["scored_at"][:10])
        full = None
        with self._lock:
            b = self._batches.get(key)
            if b is None:
                b = self._batches[key] = Batch(key[0], key[1], self.now())
            b.items.append((row, message))
            BUFFERED.inc()
            if len(b.items) >= self.batch_rows:
                full = self._batches.pop(key)
        if full:
            self.write(full)

    def flush(self, everything: bool = False) -> int:
        """Write every batch that has waited FLUSH_SECONDS (or all of them). Returns the number written."""
        with self._lock:
            due = [k for k, b in self._batches.items() if everything or self.now() - b.started >= self.flush_seconds]
            batches = [self._batches.pop(k) for k in due]
        for b in batches:
            self.write(b)
        return len(batches)

    def write(self, b: Batch) -> None:
        BUFFERED.dec(len(b.items))
        seen, rows, keep, dupes = set(), [], [], []
        for row, msg in b.items:
            (dupes if row["delivery_id"] in seen else keep).append(msg)
            if row["delivery_id"] not in seen:
                seen.add(row["delivery_id"])
                rows.append(row)
        ctx = {"tenant_id": b.tenant_id, "date": b.date, "rows": len(rows)}
        t0 = time.perf_counter()
        try:
            key = self.keys.data_key(b.tenant_id)
            if key is None:
                log.warning("tenant being deleted or gone: rows dropped", extra=ctx)
                ROWS.labels("tenant_gone").inc(len(b.items))
                for m in keep + dupes:
                    m.ack()
                return
            rows.sort(key=lambda r: r["scored_at"])
            name = files.object_name(b.tenant_id, b.date, rows[0]["scored_at"])
            blob = self.bucket.blob(name)
            blob.upload_from_string(crypto.encrypt(files.parquet_bytes(rows), key, name),
                                    content_type="application/octet-stream", if_generation_match=0)
        except Exception:  # noqa: BLE001  Postgres, KMS or the bucket: retried by Pub/Sub
            FAILURES.inc()
            log.exception("score log file not written: retrying", extra=ctx)
            for m in keep + dupes:
                m.nack()
            return
        FLUSH.observe(time.perf_counter() - t0)
        FILES.inc()
        ROWS.labels("written").inc(len(rows))
        ROWS.labels("duplicate").inc(len(dupes))
        for m in keep + dupes:
            m.ack()
        log.info("score log file written", extra={**ctx, "object": name})

    def buffered(self) -> int:
        with self._lock:
            return sum(len(b.items) for b in self._batches.values())
