"""Score log rows to one Parquet file (ADR 0024), in the flat form of ADR 0012.

Each column is a JSON Pointer of the score_log message: /delivery_id, /rank_score, /features/LA, ... (from
contracts/flatten.py, shared with every contract). A value that is not a scalar (an empty features object on an
unranked row, a list) is stored as its JSON text. Files live at

    score_log/tenant=<tenant_id>/date=<YYYY-MM-DD>/<HHMMSS>-<random>.parquet.enc

dated by the rows' scored_at (UTC), and are encrypted with the tenant's data key (crypto.py). Rows are never
rewritten; a message delivered twice may appear in two files, so readers keep one row per /delivery_id.
"""
from __future__ import annotations

import io
import json
import uuid

import pyarrow as pa
import pyarrow.parquet as pq

from flatten import flatten

SUFFIX = ".parquet.enc"


def flat_row(message: dict) -> dict:
    out = {}
    for k, v in flatten(message).items():
        out[k] = json.dumps(v, sort_keys=True) if isinstance(v, (list, dict)) else v
    return out


def table(rows: list[dict]) -> pa.Table:
    flat = [flat_row(r) for r in rows]
    columns = sorted({k for r in flat for k in r})
    data = {}
    for c in columns:
        values = [r.get(c) for r in flat]
        try:
            data[c] = pa.array(values)
        except (pa.ArrowInvalid, pa.ArrowTypeError):     # mixed types in one column: keep them as text
            data[c] = pa.array([None if v is None else json.dumps(v) for v in values], pa.string())
    return pa.table(data)


def parquet_bytes(rows: list[dict]) -> bytes:
    buf = io.BytesIO()
    pq.write_table(table(rows), buf, compression="zstd")
    return buf.getvalue()


def object_name(tenant_id: str, date: str, first_scored_at: str) -> str:
    hhmmss = first_scored_at[11:19].replace(":", "")
    return f"score_log/tenant={tenant_id}/date={date}/{hhmmss}-{uuid.uuid4().hex[:12]}{SUFFIX}"
