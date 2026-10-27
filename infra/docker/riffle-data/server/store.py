"""
store.py — where uploaded files live (ADR 0015).

Small files the viewer reads (run logs and traces, health files, run.json) are always kept
on the VM's disk under <data>/store, so log search and the pages never wait on
the network. With a bucket configured (RIFFLE_BUCKET), every file is also in
GCS, and the big ones (datasets, snapshot tables) live only there, which
is where BigQuery loads them from. With no bucket, everything stays on the disk:
that is the local and test setup.
"""
from __future__ import annotations

import fnmatch
import os
import threading
import time
import tempfile
import shutil

# keys (below the PC's name) the VM keeps a local copy of
CACHE_MAX_BYTES = int(os.environ.get("RIFFLE_CACHE_MAX_BYTES", 20 * 1024 ** 3))   # downloaded big files; the oldest go first
CACHE_KEEP_S = 600                                          # a file used this recently may be open in a request: kept
MIRRORED = ("*/logs/*.log", "*/logs/*.trace.jsonl", "*/health/*.json", "*/run.json")


def is_mirrored(key: str) -> bool:
    parts = key.split("/", 1)                               # drop the PC name
    return len(parts) == 2 and any(fnmatch.fnmatch(parts[1], pat) for pat in MIRRORED)


class Store:
    def __init__(self, root: str, bucket: str | None = None):
        self.root = os.path.join(root, "store")
        self.cache = os.path.join(root, "cache")
        os.makedirs(self.root, exist_ok=True)
        os.makedirs(self.cache, exist_ok=True)
        self.bucket_name = bucket
        self._key_locks = [threading.Lock() for _ in range(64)]
        self._bucket = None
        if bucket:
            from google.cloud import storage                # the VM's service account; no key file
            self._bucket = storage.Client().bucket(bucket)

    def lock_for(self, key: str) -> threading.Lock:
        """One lock per key (64 shared locks, so two keys may share one): held around anything that changes a stored
        file or its index row, so an append and a whole-file upload of the same trace cannot interleave."""
        return self._key_locks[hash(key) % len(self._key_locks)]

    def local_path(self, key: str) -> str:
        return os.path.join(self.root, *key.split("/"))

    def put(self, key: str, tmp_path: str) -> None:
        """Take ownership of the finished upload at tmp_path."""
        keep_local = self._bucket is None or is_mirrored(key)
        if self._bucket is not None:
            self._bucket.blob(key).upload_from_filename(tmp_path)
        if keep_local:
            dest = self.local_path(key)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.move(tmp_path, dest)
            try:
                os.chmod(dest, 0o644)                       # other containers (Airflow) read the volume read-only
            except OSError:
                pass                                        # an odd filesystem must not fail an upload
        else:
            os.remove(tmp_path)

    def append(self, key: str, offset: int, data: bytes) -> tuple[bool, int]:
        """Add `data` to the end of a mirrored file, only if the file is exactly `offset` bytes long now.
        (True, new size) or (False, current size). The bucket copy is brought up to date by `mirror`."""
        dest = self.local_path(key)
        have = os.path.getsize(dest) if os.path.isfile(dest) else 0
        if have != offset:
            return False, have
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "ab") as fh:
            fh.write(data)
        return True, have + len(data)

    def mirror(self, key: str) -> None:
        """Copy the local file to the bucket (appends only touch the disk until this runs)."""
        if self._bucket is not None and os.path.isfile(self.local_path(key)):
            self._bucket.blob(key).upload_from_filename(self.local_path(key))

    def fetch(self, key: str) -> str | None:
        """A local path for the file: the mirrored copy, or a download from the bucket into
        <data>/cache (re-downloaded only when the object's generation changes)."""
        local = self.local_path(key)
        if os.path.isfile(local):
            return local
        if self._bucket is None:
            return None
        blob = self._bucket.get_blob(key)
        if blob is None:
            return None
        dest = os.path.join(self.cache, *key.split("/"))
        stamp = dest + ".gen"
        try:
            fresh = open(stamp).read() == str(blob.generation) and os.path.isfile(dest)
        except OSError:
            fresh = False
        if not fresh:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(dest), suffix=".part")      # unique: two readers may fetch at once
            os.close(fd)
            try:
                blob.download_to_filename(tmp)
                os.replace(tmp, dest)
            except BaseException:
                if os.path.exists(tmp):
                    os.remove(tmp)
                raise
            with open(stamp, "w") as fh:
                fh.write(str(blob.generation))
        else:
            os.utime(dest)                                  # a use, for the eviction order
        self.evict(dest)
        return dest

    def evict(self, keep: str | None = None, limit: int | None = None) -> int:
        """Delete the least recently used cached downloads until the cache fits `limit` bytes (they are re-downloaded when
        asked for again). The file just fetched and anything used in the last few minutes stay. Returns bytes freed."""
        limit = CACHE_MAX_BYTES if limit is None else limit
        files, total = [], 0
        for dirpath, _d, names in os.walk(self.cache):
            for name in names:
                path = os.path.join(dirpath, name)
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                total += st.st_size
                if not name.endswith(".gen"):
                    files.append((st.st_mtime, st.st_size, path))
        freed, now = 0, time.time()
        for mtime, size, path in sorted(files):
            if total - freed <= limit:
                break
            if path == keep or now - mtime < CACHE_KEEP_S:
                continue
            try:
                os.remove(path)
                freed += size
                if os.path.exists(path + ".gen"):
                    freed += os.path.getsize(path + ".gen")
                    os.remove(path + ".gen")
            except OSError:
                pass
        return freed

    def open_local(self, key: str):
        """The local copy, or None (a big file that lives only in the bucket)."""
        path = self.local_path(key)
        return open(path, "rb") if os.path.isfile(path) else None

    def uri(self, key: str) -> str | None:
        return f"gs://{self.bucket_name}/{key}" if self.bucket_name else None
