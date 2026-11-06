"""Anomaly detection: missing values, outliers, invalid formats, schema violations and
contradictions between columns.

Each check is a plain function from a DataFrame to a list of Anomaly records, so it can be
tested alone. `error` means the data cannot be used as it is (the stage fails); `warn` means
it can, but someone should look (an alert goes out).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd
from pandas.api import types

from scripts import schema

EXAMPLE_LIMIT = 5
CONSISTENCY_ERROR_RATE = 0.001  # above 0.1% of rows, a contradiction fails the stage


@dataclass
class Anomaly:
    kind: str  # schema | missing | format | outlier | consistency | reference
    severity: str  # error | warn
    table: str
    column: str
    count: int
    total: int
    message: str
    examples: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


_KIND_TESTS = {
    "int": types.is_integer_dtype,
    "str": types.is_string_dtype,
    "bool": types.is_bool_dtype,
}


def _examples(df: pd.DataFrame, mask: pd.Series) -> list:
    """Ids of the first few offending rows, as plain Python values."""
    return [int(v) for v in df.loc[mask, "id"].head(EXAMPLE_LIMIT)] if "id" in df else []


def schema_violations(df: pd.DataFrame, table: str) -> list[Anomaly]:
    """Missing or unexpected columns and columns of the wrong kind."""
    expected = schema.SCHEMAS[table]
    found: list[Anomaly] = []
    for column in sorted(set(expected) - set(df.columns)):
        found.append(Anomaly("schema", "error", table, column, 1, 1, "column is missing"))
    for column in sorted(set(df.columns) - set(expected)):
        found.append(Anomaly("schema", "error", table, column, 1, 1, "unexpected column"))
    for column, kind in expected.items():
        if column in df.columns and not _KIND_TESTS[kind](df[column]):
            found.append(
                Anomaly("schema", "error", table, column, 1, 1, f"expected {kind}, found {df[column].dtype}")
            )
    return found


def missing_values(df: pd.DataFrame, table: str) -> list[Anomaly]:
    """Columns whose share of nulls is above what the schema tolerates."""
    allowed = schema.MAX_NULL_RATE.get(table, {})
    found = []
    for column in schema.SCHEMAS[table]:
        if column not in df.columns:
            continue
        nulls = df[column].isna()
        rate = float(nulls.mean()) if len(df) else 0.0
        limit = allowed.get(column, 0.0)
        if rate > limit:
            found.append(
                Anomaly(
                    "missing",
                    "error",
                    table,
                    column,
                    int(nulls.sum()),
                    len(df),
                    f"{rate:.2%} null, more than the {limit:.2%} tolerated",
                    _examples(df, nulls),
                )
            )
    return found


def invalid_formats(df: pd.DataFrame, table: str) -> list[Anomaly]:
    """Non-null values that do not match their column's format."""
    patterns = {
        "pull_request": {
            "created_at": schema.TIMESTAMP_REGEX,
            "closed_at": schema.TIMESTAMP_REGEX,
            "merged_at": schema.TIMESTAMP_REGEX,
            "html_url": schema.PR_URL_REGEX,
        },
        "repository": {"full_name": schema.FULL_NAME_REGEX},
    }[table]
    found = []
    for column, pattern in patterns.items():
        if column not in df.columns:
            continue
        values = df[column]
        bad = values.notna() & ~values.fillna("").astype(str).str.match(pattern)
        if bad.any():
            found.append(
                Anomaly(
                    "format",
                    "error",
                    table,
                    column,
                    int(bad.sum()),
                    int(values.notna().sum()),
                    f"does not match {pattern}",
                    _examples(df, bad),
                )
            )
    return found


def outliers(series: pd.Series, table: str, column: str, factor: float, log_scale: bool = True) -> list[Anomaly]:
    """Values beyond `factor` times the interquartile range from the quartiles.

    Counts such as stars span five orders of magnitude, so the range is measured on log1p;
    on the raw scale a fifth of all repositories would count as outliers.
    """
    values = series.dropna().astype(float)
    values = values[values >= 0] if log_scale else values
    if len(values) < 20:
        return []
    scaled = np.log1p(values) if log_scale else values
    q1, q3 = scaled.quantile(0.25), scaled.quantile(0.75)
    spread = q3 - q1
    low, high = q1 - factor * spread, q3 + factor * spread
    mask = (scaled < low) | (scaled > high)
    if not mask.any():
        return []
    shown = np.expm1(high) if log_scale else high
    return [
        Anomaly(
            "outlier",
            "warn",
            table,
            column,
            int(mask.sum()),
            len(values),
            f"values beyond {factor:g} x IQR (above {shown:,.0f})",
            [float(v) for v in values[mask].nlargest(EXAMPLE_LIMIT)],
        )
    ]


def contradiction_masks(pr: pd.DataFrame) -> dict[str, pd.Series]:
    """For each rule a pull request can break, a boolean mask of the rows that break it.

    preprocess quarantines exactly these rows, so the rules live in one place.
    """
    created = pd.to_datetime(pr["created_at"], utc=True, errors="coerce")
    closed = pd.to_datetime(pr["closed_at"], utc=True, errors="coerce")
    merged = pd.to_datetime(pr["merged_at"], utc=True, errors="coerce")
    return {
        "closed_at before created_at": closed < created,
        "merged_at before created_at": merged < created,
        "merged_at set but closed_at empty": merged.notna() & closed.isna(),
        "state is open but closed_at is set": (pr["state"] == "open") & closed.notna(),
        "state is closed but closed_at is empty": (pr["state"] == "closed") & closed.isna(),
    }


def consistency(pr: pd.DataFrame) -> list[Anomaly]:
    """Pull requests whose own columns contradict each other."""
    found = []
    for message, mask in contradiction_masks(pr).items():
        if mask.any():
            # A handful of bad rows is quarantined and reported; a sudden jump means the source broke.
            severity = "error" if mask.mean() > CONSISTENCY_ERROR_RATE else "warn"
            found.append(
                Anomaly("consistency", severity, "pull_request", "created_at", int(mask.sum()), len(pr), message, _examples(pr, mask))
            )
    return found


def referential(pr: pd.DataFrame, repo: pd.DataFrame) -> list[Anomaly]:
    """Pull requests whose repo_id is not in the repository table."""
    orphan = ~pr["repo_id"].isin(repo["id"])
    if not orphan.any():
        return []
    return [
        Anomaly(
            "reference",
            "error",
            "pull_request",
            "repo_id",
            int(orphan.sum()),
            len(pr),
            "repo_id not found in repository",
            _examples(pr, orphan),
        )
    ]


def detect(frames: dict[str, pd.DataFrame], iqr_factor: float = 3.0) -> list[Anomaly]:
    """Run every check. Schema violations come first and stop the rest for that table."""
    found: list[Anomaly] = []
    clean = {}
    for table, df in frames.items():
        problems = schema_violations(df, table)
        found += problems
        if not problems:
            clean[table] = df
    for table, df in clean.items():
        found += missing_values(df, table) + invalid_formats(df, table)
    if "repository" in clean:
        repo = clean["repository"]
        found += outliers(repo["stars"], "repository", "stars", iqr_factor)
        found += outliers(repo["forks"], "repository", "forks", iqr_factor)
    if "pull_request" in clean:
        pr = clean["pull_request"]
        if not any(a.table == "pull_request" and a.kind == "format" for a in found):
            found += consistency(pr)
            opened = pd.to_datetime(pr["created_at"], utc=True, errors="coerce")
            closed = pd.to_datetime(pr["closed_at"], utc=True, errors="coerce")
            hours = (closed - opened).dt.total_seconds() / 3600
            found += outliers(hours[hours >= 0], "pull_request", "hours_open", iqr_factor)
        if "repository" in clean:
            found += referential(pr, clean["repository"])
    return found
