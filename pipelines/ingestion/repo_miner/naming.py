"""
naming.py — names for a run's output dataset and its logs.

With several PCs each mining a share (--shard K/N), every PC writing the same
`dataset.parquet` makes the files easy to mix up and overwrite when they are
copied together. So a sharded run tags its names: `dataset.parquet` becomes
`dataset_pc1of3.parquet`, and its log `run-<time>-pc1of3.log`.

  --out-prefix / --out-postfix add your own text (placeholders {pc} {n} {host}
  {date}); a postfix replaces the automatic pc tag. --no-auto-name turns the
  automatic tag off. A name that already carries its tag is left alone, so
  rerunning with the tagged name resumes the same file.
"""
from __future__ import annotations

import os
import re
import socket
import time


def parse_shard(shard: str | None) -> tuple[int, int] | None:
    """(K, N) from "K/N", or None when absent, malformed or N == 1."""
    m = re.fullmatch(r"\s*(\d+)\s*/\s*(\d+)\s*", shard or "")
    if not m:
        return None
    k, n = int(m.group(1)), int(m.group(2))
    return (k, n) if 1 <= k <= n and n > 1 else None


def pc_tag(shard: str | None) -> str:
    kn = parse_shard(shard)
    return f"pc{kn[0]}of{kn[1]}" if kn else ""


def _host() -> str:
    return re.sub(r"[^a-z0-9-]+", "-", socket.gethostname().split(".")[0].lower()).strip("-") or "host"


def _expand(text: str, shard: str | None) -> str:
    kn = parse_shard(shard)
    vals = {"pc": kn[0] if kn else 1, "n": kn[1] if kn else 1, "host": _host(),
            "date": time.strftime("%Y%m%d")}
    try:
        text = text.format(**vals)
    except (KeyError, IndexError, ValueError):
        pass                                   # unknown placeholder: keep the text as typed
    return re.sub(r"[^\w.\-]+", "-", text)     # nothing that is awkward in a file name


def tagged_out(out: str, shard: str | None = None, prefix: str = "", postfix: str = "",
               auto: bool = True) -> str:
    """`out` with the run's prefix and postfix applied (see the module docstring)."""
    tag = _expand(postfix, shard) if postfix else (pc_tag(shard) if auto else "")
    pre = _expand(prefix, shard) if prefix else ""
    if not tag and not pre:
        return out
    folder, base = os.path.split(out)
    stem, ext = os.path.splitext(base)
    if tag and not stem.endswith("_" + tag):
        stem = f"{stem}_{tag}"
    if pre and not stem.startswith(pre):
        stem = pre + ("" if pre[-1] in "-_." else "_") + stem
    return os.path.join(folder, stem + ext)


def snapshot_outs(out: str) -> tuple[str, str]:
    """The snapshot table and the commit-feature table that go with dataset
    `out`: `dataset_pc1of3.parquet` -> `dataset_pc1of3_snapshots.parquet` and
    `dataset_pc1of3_commit_features.parquet`, so the three files sort together
    and always say which PC and run they came from."""
    stem, ext = os.path.splitext(out)
    return f"{stem}_snapshots{ext}", f"{stem}_commit_features{ext}"


def manifest_path(out: str) -> str:
    """The small JSON written beside a sharded dataset (`dataset_pc1of3.shard.json`):
    which split, PC and miner version produced it, so merge_shards.py can check
    that the pieces fit before it joins them."""
    return os.path.splitext(out)[0] + ".shard.json"


def run_stem(shard: str | None = None) -> str:
    """`run-YYYYmmdd-HHMMSS`, plus `-pc1of3` for a sharded run: the stem of the
    run log and its trace."""
    tag = pc_tag(shard)
    return time.strftime("run-%Y%m%d-%H%M%S") + (f"-{tag}" if tag else "")
