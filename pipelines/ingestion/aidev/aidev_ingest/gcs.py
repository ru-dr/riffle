"""The real bucket: a thin wrapper over google-cloud-storage.

Imported only when an upload actually runs, so everything else, including CI's tests, works
without the library. Credentials are Application Default Credentials; no key file is read.

Two things learned from a real run that dropped its connection partway through a large file:
chunks are small (8 MiB), so a dropped connection costs little, and the retry deadline is
15 minutes instead of Google's default 2, so a brief outage does not end the run. A failure
that outlasts that becomes a short UploadError, not a traceback. An unfinished resumable
upload leaves no object, so rerunning is always safe.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from .upload import ObjectExists, RemoteObject, UploadError

CHUNK_SIZE = 8 * 1024 * 1024  # a multiple of 256 KiB, as resumable uploads require
TIMEOUT_SECONDS = 600
RETRY_DEADLINE_SECONDS = 900.0


def _remote(blob) -> RemoteObject:
    return RemoteObject(
        name=blob.name,
        size=int(blob.size),
        md5_b64=blob.md5_hash or "",
        metadata=dict(blob.metadata or {}),
    )


class GcsBucket:
    def __init__(self, name: str, client=None) -> None:
        try:
            from google.api_core import exceptions
            from google.auth import exceptions as auth_exceptions
            from google.cloud import storage
            from google.cloud.storage import retry as storage_retry
            from google.resumable_media import common as media
        except ImportError:
            raise RuntimeError(
                "google-cloud-storage is not installed: pip install google-cloud-storage "
                "(or pip install 'riffle-aidev-ingest[gcs]')"
            ) from None
        import requests

        self._exceptions = exceptions
        # Everything that means "the network or Google let us down", as opposed to a bug here.
        self._transport_errors = (
            exceptions.GoogleAPIError,
            requests.exceptions.RequestException,
            media.InvalidResponse,
            media.DataCorruption,
            ConnectionError,
            TimeoutError,
        )
        # Uploads here always carry if_generation_match, so retrying them is safe: a retry can
        # never overwrite anything.
        self._retry = storage_retry.ConditionalRetryPolicy(
            storage_retry.DEFAULT_RETRY.with_deadline(RETRY_DEADLINE_SECONDS),
            storage_retry.is_generation_specified,
            ["query_params"],
        )
        try:
            self._client = client or storage.Client()
        except auth_exceptions.DefaultCredentialsError:
            raise UploadError(
                "no Google credentials found. Sign in with `gcloud auth application-default login` "
                "(no key file is used) and run the command again."
            ) from None
        self._bucket = self._client.bucket(name)

    @contextmanager
    def _translated(self, what: str):
        try:
            yield
        except self._exceptions.PreconditionFailed:
            raise  # "already exists" is an answer, not a failure; callers handle it
        except self._transport_errors as error:
            code = getattr(error, "code", None)
            detail = type(error).__name__ + (f", HTTP {code}" if isinstance(code, int) else "")
            # A 4xx other than "timed out" or "slow down" is Google answering, not the network
            # failing: permissions or a wrong bucket name, which running again will not fix.
            refused = isinstance(code, int) and 400 <= code < 500 and code not in (408, 429)
            hint = (
                " Google refused the request: check your credentials, the bucket name and the "
                "account's permissions."
                if refused
                else " Probably the network."
            )
            raise UploadError(
                f"{what}: could not finish ({detail}).{hint} "
                "Nothing local was deleted by this step. An interrupted upload normally leaves no object, but if "
                "the failure came after it finished one may exist; the next run checks it and keeps "
                "your local copy if it is wrong. Run the same command again to continue."
            ) from None

    def get(self, name: str) -> RemoteObject | None:
        with self._translated(f"reading {name}"):
            blob = self._bucket.get_blob(name)
        return None if blob is None else _remote(blob)

    def create(self, name: str, path: Path, metadata: dict[str, str]) -> RemoteObject:
        """Create the object only if it does not exist (generation 0), and have the client
        check the md5 of what arrived. Raises ObjectExists if the object is already there."""
        blob = self._bucket.blob(name, chunk_size=CHUNK_SIZE)
        blob.metadata = metadata
        try:
            with self._translated(f"uploading {name}"):
                blob.upload_from_filename(
                    str(path),
                    content_type="application/vnd.apache.parquet",
                    if_generation_match=0,
                    checksum="md5",
                    timeout=TIMEOUT_SECONDS,
                    retry=self._retry,
                )
        except self._exceptions.PreconditionFailed:
            raise ObjectExists(name) from None
        with self._translated(f"reading {name} back"):
            blob.reload()
        return _remote(blob)

    def create_text(self, name: str, text: str) -> None:
        blob = self._bucket.blob(name)
        try:
            with self._translated(f"writing {name}"):
                blob.upload_from_string(
                    text, content_type="application/json", if_generation_match=0, retry=self._retry
                )
        except self._exceptions.PreconditionFailed:
            raise ObjectExists(name) from None

    def list(self, prefix: str) -> list[RemoteObject]:
        with self._translated(f"listing {prefix}"):
            return [_remote(b) for b in self._client.list_blobs(self._bucket, prefix=prefix)]
