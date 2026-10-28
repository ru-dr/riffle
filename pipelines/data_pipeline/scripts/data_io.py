"""Reading the acquired tables. Every later stage loads data through here."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from scripts.schema import SCHEMAS

# Free-text columns. The checks only ask whether they are null, never what they say, and the
# pull request bodies are most of the table's memory.
TEXT_COLUMNS = ("title", "body", "user")
BATCH_ROWS = 50_000


class MissingTable(Exception):
    """A table the pipeline needs is not in the raw folder."""


def _read_without_text(path: Path) -> pd.DataFrame:
    """Read a table in batches, keeping only each text value's first character.

    Nulls stay null and an empty string stays empty, so null-rate checks are unchanged, while the
    full text never sits in memory. Columns that are not text are left alone.
    """
    parquet = pq.ParquetFile(path)
    pieces = []
    for batch in parquet.iter_batches(batch_size=BATCH_ROWS):
        table = pa.Table.from_batches([batch])
        for name in TEXT_COLUMNS:
            if name in table.column_names and (pa.types.is_string(table[name].type) or pa.types.is_large_string(table[name].type)):
                position = table.column_names.index(name)
                table = table.set_column(position, name, pc.utf8_slice_codeunits(table[name], 0, 1))
        pieces.append(table)
    if not pieces:  # an empty table has no batches
        return parquet.read().to_pandas()
    return pa.concat_tables(pieces).to_pandas()


def load_raw(
    raw_root: Path,
    commit: str,
    tables: tuple[str, ...] = tuple(SCHEMAS),
    text: bool = True,
) -> dict[str, pd.DataFrame]:
    """Read each table from <raw_root>/<commit>/<table>.parquet.

    With text=False the free-text columns are cut to one character (see _read_without_text), which
    is what the validation stage uses.
    """
    frames = {}
    for table in tables:
        path = raw_root / commit / f"{table}.parquet"
        if not path.is_file():
            raise MissingTable(f"{path} does not exist; run the acquire stage first")
        frames[table] = pd.read_parquet(path) if text else _read_without_text(path)
    return frames
