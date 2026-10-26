"""Anonymous, read-only access to the Hugging Face Hub.

Only file listings and Parquet footers are read, through range requests, so an
inventory costs a few megabytes however large the tables are. No token is sent:
AIDev is public, and a stale token in the local cache would otherwise turn every
call into a 401.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import IO, Iterator, Protocol

# Must be set before huggingface_hub is imported.
os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")

import pyarrow.parquet as pq  # noqa: E402
from huggingface_hub import HfApi, HfFileSystem  # noqa: E402
from huggingface_hub.hf_api import RepoFile  # noqa: E402

from .pin import REPO_ID, REPO_TYPE  # noqa: E402

FULL_HASH = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class FileInfo:
    path: str
    size: int
    lfs_sha256: str | None
    blob_id: str | None


@dataclass(frozen=True)
class ParquetInfo:
    rows: int
    row_groups: int
    columns: tuple[tuple[str, str], ...]  # (name, arrow type)


class Source(Protocol):
    """What the inventory needs. Tests supply a fake; HubSource is the real one."""

    def resolve(self, ref: str) -> str: ...
    def list_files(self, revision: str) -> Iterator[FileInfo]: ...
    def parquet_info(self, path: str, revision: str) -> ParquetInfo: ...


def summarize_parquet(file: IO[bytes]) -> ParquetInfo:
    """Read a Parquet footer. Works on any seekable file, remote or local."""
    parquet_file = pq.ParquetFile(file)
    meta = parquet_file.metadata
    columns = tuple((field.name, str(field.type)) for field in parquet_file.schema_arrow)
    return ParquetInfo(rows=meta.num_rows, row_groups=meta.num_row_groups, columns=columns)


def _lfs_sha256(entry: RepoFile) -> str | None:
    lfs = getattr(entry, "lfs", None)
    if not lfs:
        return None
    return lfs["sha256"] if isinstance(lfs, dict) else getattr(lfs, "sha256", None)


class HubSource:
    def __init__(self, repo_id: str = REPO_ID) -> None:
        self.repo_id = repo_id
        self._api = HfApi(token=False)
        self._fs = HfFileSystem(token=False)

    def resolve(self, ref: str) -> str:
        """Turn a tag, branch or full hash into the commit hash it names now.

        Not `dataset_info(...).sha`: for this dataset it does not return the commit
        that was asked for (it gave one hash for tag v3 where list_repo_refs gives
        another, and a different hash again when handed a full commit id).
        """
        if FULL_HASH.match(ref):
            # A full hash needs no resolving, only a check that it exists. Raises
            # RevisionNotFoundError if not.
            next(iter(self._api.list_repo_tree(self.repo_id, repo_type=REPO_TYPE, revision=ref)), None)
            return ref
        refs = self._api.list_repo_refs(self.repo_id, repo_type=REPO_TYPE)
        for named in (*refs.tags, *refs.branches):
            if named.name == ref:
                return named.target_commit
        raise ValueError(f"{self.repo_id} has no tag or branch named {ref!r}")

    def list_files(self, revision: str) -> Iterator[FileInfo]:
        tree = self._api.list_repo_tree(
            self.repo_id, repo_type=REPO_TYPE, revision=revision, recursive=True
        )
        for entry in tree:
            if isinstance(entry, RepoFile):
                yield FileInfo(
                    path=entry.path,
                    size=entry.size,
                    lfs_sha256=_lfs_sha256(entry),
                    blob_id=entry.blob_id,
                )

    def parquet_info(self, path: str, revision: str) -> ParquetInfo:
        with self._fs.open(f"datasets/{self.repo_id}@{revision}/{path}", "rb") as handle:
            return summarize_parquet(handle)
