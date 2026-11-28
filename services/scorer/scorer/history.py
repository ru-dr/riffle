"""The repo's earlier PRs, which the 30 history features are built from (ADR 0027).

One Parquet file of mined_dataset rows per repository, in the snapshots bucket:

    gs://$BUCKET_SNAPSHOTS/history/tenant=<tenant_id>/repo=<owner>__<name>.parquet

It is filled at install by running the miner on the repo (the backfill) and grows as PRs close. A repository with no
file yet has no history: every history feature is then what it is for a repo's first PR, as in training. Files are
cached in memory for CACHE_SECONDS. build_features keeps point-in-time: only PRs that closed before this PR opened
count.

Not done yet: the files are not encrypted with the tenant's key (ADR 0025); the writer (backfill and outcome
labeller) is not built.
"""
from __future__ import annotations

import io
import os
import threading
import time

import pandas as pd

EMPTY = pd.DataFrame()


def object_name(tenant_id: str, repo: str) -> str:
    return f"history/tenant={tenant_id}/repo={repo.replace('/', '__')}.parquet"


class HistoryStore:
    def __init__(self, bucket: str, cache_seconds: float = 300.0, max_repos: int = 256, client=None):
        self.bucket_name = bucket
        self.cache_seconds, self.max_repos = cache_seconds, max_repos
        self._client = client
        self._cache: dict[tuple[str, str], tuple[float, pd.DataFrame]] = {}
        self._lock = threading.Lock()

    def _bucket(self):
        if self._client is None:
            from google.cloud import storage
            self._client = storage.Client()
        return self._client.bucket(self.bucket_name)

    def rows(self, tenant_id: str, repo: str) -> pd.DataFrame:
        """The repo's stored rows (empty when there is no file). Raises when the store cannot be read: scoring
        without the history the repo has would give different features, so the message is retried instead."""
        key = (tenant_id, repo)
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(key)
            if hit and now - hit[0] < self.cache_seconds:
                return hit[1]
        blob = self._bucket().blob(object_name(tenant_id, repo))
        from google.api_core.exceptions import NotFound
        try:
            data = blob.download_as_bytes()
        except NotFound:
            df = EMPTY
        else:
            df = pd.read_parquet(io.BytesIO(data))
            df = df[df["repo"] == repo].reset_index(drop=True)
        with self._lock:
            if len(self._cache) >= self.max_repos:
                self._cache.pop(min(self._cache, key=lambda k: self._cache[k][0]))
            self._cache[key] = (now, df)
        return df


def from_env() -> HistoryStore:
    return HistoryStore(bucket=os.environ["BUCKET_SNAPSHOTS"],
                        cache_seconds=float(os.environ.get("HISTORY_CACHE_SECONDS", "300")))
