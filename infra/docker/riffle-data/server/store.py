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
import tempfile
import shutil

# keys (below the PC's name) the VM keeps a local copy of
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
        self._bucket = None
        if bucket:
            from google.cloud import storage                # the VM's service account; no key file
            self._bucket = storage.Client().bucket(bucket)

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
        else:
            os.remove(tmp_path)

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
        return dest

    def open_local(self, key: str):
        """The local copy, or None (a big file that lives only in the bucket)."""
        path = self.local_path(key)
        return open(path, "rb") if os.path.isfile(path) else None

    def uri(self, key: str) -> str | None:
        return f"gs://{self.bucket_name}/{key}" if self.bucket_name else None
