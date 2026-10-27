"""Reading the acquired tables. Every later stage loads data through here."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from scripts.schema import SCHEMAS


class MissingTable(Exception):
    """A table the pipeline needs is not in the raw folder."""


def load_raw(raw_root: Path, commit: str, tables: tuple[str, ...] = tuple(SCHEMAS)) -> dict[str, pd.DataFrame]:
    """Read each table from <raw_root>/<commit>/<table>.parquet."""
    frames = {}
    for table in tables:
        path = raw_root / commit / f"{table}.parquet"
        if not path.is_file():
            raise MissingTable(f"{path} does not exist; run the acquire stage first")
        frames[table] = pd.read_parquet(path)
    return frames
