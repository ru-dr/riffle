"""Each tenant's data key: kept in Postgres (tenant_keys) wrapped by the KMS key tenant-kek, unwrapped in
memory for CACHE_SECONDS (ADR 0028).

The tenant workflow (ADR 0025) is meant to make the key at install. Until it exists, and in case a key is
missing, the first writer that needs one makes it: a new random key, wrapped by KMS with the tenant id as
context, inserted with ON CONFLICT DO NOTHING and read back, so two writers racing end with the same key.
Keys are made only for tenants that are active or suspended: a tenant being deleted, or gone, gets none and
its rows are dropped (its data is being deleted anyway).
"""
from __future__ import annotations

import logging
import threading
import time

from . import crypto

log = logging.getLogger("scorelog_writer.keys")

MAKES_KEY = ("active", "suspended")


def context(tenant_id: str) -> bytes:
    """KMS associated data: a wrapped key only unwraps for the tenant it was made for."""
    return f"riffle-tenant-data-key:{tenant_id}".encode()


class Kms:
    """Wraps and unwraps with the KMS key tenant-kek (TENANT_KEK_ID)."""

    def __init__(self, kek_name: str, client=None):
        self.kek_name = kek_name
        self._client = client

    def client(self):
        if self._client is None:
            from google.cloud import kms
            self._client = kms.KeyManagementServiceClient()
        return self._client

    def wrap(self, tenant_id: str, key: bytes) -> bytes:
        return self.client().encrypt(request={"name": self.kek_name, "plaintext": key,
                                              "additional_authenticated_data": context(tenant_id)}).ciphertext

    def unwrap(self, tenant_id: str, kek_name: str, wrapped: bytes) -> bytes:
        return self.client().decrypt(request={"name": kek_name, "ciphertext": wrapped,
                                              "additional_authenticated_data": context(tenant_id)}).plaintext


class Db:
    """tenant_keys and the tenant's status, under row-level security (app.tenant_id per transaction)."""

    def __init__(self, conninfo: str):
        self.conninfo = conninfo
        self._local = threading.local()

    def _run(self, tenant_id: str, fn):
        import psycopg
        c = getattr(self._local, "conn", None)
        if c is None or c.closed or c.broken:
            c = self._local.conn = psycopg.connect(self.conninfo, autocommit=False, connect_timeout=10)
        try:
            with c.transaction():
                c.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
                return fn(c)
        except Exception:
            if c.broken:
                self._local.conn = None
            raise

    def get(self, tenant_id: str):
        """(wrapped_key, kek_name) or None."""
        row = self._run(tenant_id, lambda c: c.execute(
            "SELECT wrapped_key, kek_name FROM tenant_keys WHERE tenant_id = %s", (tenant_id,)).fetchone())
        return (bytes(row[0]), row[1]) if row else None

    def status(self, tenant_id: str) -> str | None:
        row = self._run(tenant_id, lambda c: c.execute(
            "SELECT status FROM tenants WHERE tenant_id = %s", (tenant_id,)).fetchone())
        return row[0] if row else None

    def insert(self, tenant_id: str, wrapped: bytes, kek_name: str):
        self._run(tenant_id, lambda c: c.execute(
            "INSERT INTO tenant_keys (tenant_id, wrapped_key, kek_name) VALUES (%s, %s, %s) "
            "ON CONFLICT (tenant_id) DO NOTHING", (tenant_id, wrapped, kek_name)))


class KeyStore:
    def __init__(self, db, kms: Kms, cache_seconds: float = 600.0, now=time.monotonic):
        self.db, self.kms, self.cache_seconds, self.now = db, kms, cache_seconds, now
        self._cache: dict[str, tuple[float, bytes]] = {}
        self._lock = threading.Lock()

    def data_key(self, tenant_id: str) -> bytes | None:
        """The tenant's plain data key; None when the tenant is being deleted or does not exist. Raises when
        Postgres or KMS cannot be reached: the rows then wait in the queue (ADR 0025)."""
        with self._lock:
            hit = self._cache.get(tenant_id)
            if hit and self.now() - hit[0] < self.cache_seconds:
                return hit[1]
        stored = self.db.get(tenant_id)
        if stored is None:
            status = self.db.status(tenant_id)
            if status not in MAKES_KEY:
                return None
            self.db.insert(tenant_id, self.kms.wrap(tenant_id, crypto.new_key()), self.kms.kek_name)
            stored = self.db.get(tenant_id)          # the first insert wins if two writers raced
            log.info("data key made", extra={"tenant_id": tenant_id})
        key = self.kms.unwrap(tenant_id, stored[1], stored[0])
        if len(key) != crypto.KEY_BYTES:
            raise ValueError(f"tenant {tenant_id}: data key is {len(key)} bytes, not {crypto.KEY_BYTES}")
        with self._lock:
            self._cache[tenant_id] = (self.now(), key)
        return key
