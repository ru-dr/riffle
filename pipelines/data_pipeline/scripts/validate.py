"""Stage 2, validate: check the raw tables against their schema and look for anomalies.

Run from pipelines/data_pipeline:  python -m scripts.validate

Three reports go to reports/:
  schema_stats.json  every column's type, null rate and summary statistics
  validation.json    the Great Expectations result for each table, one line per expectation
  anomalies.json     missing values, outliers, invalid formats, schema violations, contradictions

The stage exits 1 when an expectation fails or an anomaly has severity `error`. A `warn`
anomaly does not stop the pipeline; it is in the report, and the Airflow DAG alerts on it.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

os.environ.setdefault("TQDM_DISABLE", "1")  # Great Expectations draws progress bars into the log

import great_expectations as gx  # noqa: E402
import great_expectations.expectations as gxe  # noqa: E402
import pandas as pd  # noqa: E402
import yaml  # noqa: E402
from pandas.api import types  # noqa: E402

from scripts import anomalies, data_io, schema  # noqa: E402
from scripts.logging_setup import ROOT, setup_logging  # noqa: E402

logging.getLogger("great_expectations").setLevel(logging.WARNING)
log = logging.getLogger("validate")

DEFAULT_MIN_ROWS = {"pull_request": 100_000, "repository": 1_000}
MAX_ROWS = 5_000_000

PRIVATE_COLUMNS = {"user", "title", "body"}  # never list their values in a report


def expectations(table: str, min_rows: int) -> list:
    """The Great Expectations suite for one table: schema, formats, values and size."""
    columns = list(schema.SCHEMAS[table])
    allowed = schema.MAX_NULL_RATE.get(table, {})
    suite: list = [gxe.ExpectTableColumnsToMatchSet(column_set=columns, exact_match=True)]
    suite.append(gxe.ExpectColumnValuesToBeUnique(column="id"))
    for column in columns:
        limit = allowed.get(column, 0.0)
        suite.append(gxe.ExpectColumnValuesToNotBeNull(column=column, mostly=1.0 - limit))

    if table == "pull_request":
        suite += [
            gxe.ExpectTableRowCountToBeBetween(min_value=min_rows, max_value=MAX_ROWS),
            gxe.ExpectColumnValuesToBeInSet(column="agent", value_set=schema.AGENTS),
            gxe.ExpectColumnValuesToBeInSet(column="state", value_set=schema.STATES),
            gxe.ExpectColumnValuesToBeBetween(column="number", min_value=1),
            gxe.ExpectColumnValuesToMatchRegex(column="html_url", regex=schema.PR_URL_REGEX),
        ]
        for column in ("created_at", "closed_at", "merged_at"):
            suite.append(gxe.ExpectColumnValuesToMatchRegex(column=column, regex=schema.TIMESTAMP_REGEX))
    else:
        suite += [
            gxe.ExpectTableRowCountToBeBetween(min_value=min_rows, max_value=MAX_ROWS),
            gxe.ExpectColumnValuesToBeBetween(column="stars", min_value=0, max_value=10_000_000),
            gxe.ExpectColumnValuesToBeBetween(column="forks", min_value=0, max_value=10_000_000),
            gxe.ExpectColumnValuesToMatchRegex(column="full_name", regex=schema.FULL_NAME_REGEX),
        ]
    return suite


def run_expectations(frames: dict[str, pd.DataFrame], min_rows: dict[str, int]) -> dict[str, Any]:
    """Validate each table; return a compact, JSON-ready summary."""
    context = gx.get_context(mode="ephemeral")
    source = context.data_sources.add_pandas("riffle")
    summary: dict[str, Any] = {}
    for table, df in frames.items():
        asset = source.add_dataframe_asset(table)
        batch = asset.add_batch_definition_whole_dataframe("whole").get_batch(batch_parameters={"dataframe": df})
        suite = gx.ExpectationSuite(name=table)
        for expectation in expectations(table, min_rows[table]):
            suite.add_expectation(expectation)
        result = batch.validate(suite)
        lines = []
        for item in result.to_json_dict()["results"]:
            config = item["expectation_config"]
            detail = item.get("result") or {}
            lines.append(
                {
                    "expectation": config["type"],
                    "column": config["kwargs"].get("column"),
                    "success": item["success"],
                    "unexpected_count": detail.get("unexpected_count"),
                }
            )
        summary[table] = {
            "success": bool(result.success),
            "evaluated": len(lines),
            "failed": sum(1 for line in lines if not line["success"]),
            "results": lines,
        }
        log.info("%s: %d expectations, %d failed", table, len(lines), summary[table]["failed"])
    return summary


def profile(df: pd.DataFrame) -> dict[str, Any]:
    """Type, null rate and summary statistics for every column."""
    columns: dict[str, Any] = {}
    for name in df.columns:
        series = df[name]
        info: dict[str, Any] = {"dtype": str(series.dtype), "null_rate": round(float(series.isna().mean()), 6)}
        if name not in PRIVATE_COLUMNS:  # these arrive cut to one character, so a distinct count would be wrong
            info["unique"] = int(series.nunique())
        if types.is_bool_dtype(series):
            info["true_rate"] = round(float(series.mean()), 6)
        elif types.is_numeric_dtype(series):
            info.update(
                min=float(series.min()),
                max=float(series.max()),
                mean=round(float(series.mean()), 4),
                median=float(series.median()),
                std=round(float(series.std()), 4),
            )
        elif name not in PRIVATE_COLUMNS and info["unique"] <= 50:
            info["top_values"] = {str(k): int(v) for k, v in series.value_counts().head(10).items()}
        columns[name] = info
    return {"rows": int(len(df)), "columns": columns}


def write_json(path: Path, document: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run(
    frames: dict[str, pd.DataFrame], reports: Path, iqr_factor: float, min_rows: dict[str, int] | None = None
) -> int:
    """Write the three reports. Returns 0 when nothing failed, 1 otherwise."""
    min_rows = {**DEFAULT_MIN_ROWS, **(min_rows or {})}
    stats = {table: profile(df) for table, df in frames.items()}
    write_json(reports / "schema_stats.json", stats)

    # A table with the wrong columns cannot be validated meaningfully, so it only gets the anomaly check.
    found = anomalies.detect(frames, iqr_factor)
    broken = {a.table for a in found if a.kind == "schema"}
    checkable = {table: df for table, df in frames.items() if table not in broken}
    validation = run_expectations(checkable, min_rows) if checkable else {}
    write_json(reports / "validation.json", validation)

    errors = [a for a in found if a.severity == "error"]
    warnings = [a for a in found if a.severity == "warn"]
    write_json(
        reports / "anomalies.json",
        {"errors": len(errors), "warnings": len(warnings), "anomalies": [a.to_dict() for a in found]},
    )
    for a in found:
        level = logging.ERROR if a.severity == "error" else logging.WARNING
        log.log(level, "%s %s.%s: %s (%d of %d)", a.kind, a.table, a.column, a.message, a.count, a.total)

    failed = [table for table, result in validation.items() if not result["success"]]
    if failed:
        log.error("great expectations failed for: %s", ", ".join(failed))
    log.info("validate: %d errors, %d warnings; reports in %s", len(errors), len(warnings), reports)
    return 1 if errors or failed else 0


def load_params(path: Path) -> dict[str, Any]:
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    commit = (document.get("aidev") or {}).get("commit")
    section = document.get("validation") or {}
    factor = section.get("iqr_factor", 3.0)
    min_rows = section.get("min_rows", {})
    if not commit:
        raise ValueError(f"{path} has no aidev.commit")
    if not isinstance(factor, (int, float)) or factor <= 0:
        raise ValueError("validation.iqr_factor must be a positive number")
    if not isinstance(min_rows, dict) or not all(isinstance(v, int) and v >= 0 for v in min_rows.values()):
        raise ValueError("validation.min_rows must map a table name to a whole number")
    return {"commit": commit, "iqr_factor": float(factor), "min_rows": min_rows}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts.validate", description=__doc__.splitlines()[0])
    parser.add_argument("--params", type=Path, default=ROOT / "params.yaml")
    parser.add_argument("--raw", type=Path, default=ROOT / "data" / "raw", help="parent of the <commit> folder")
    parser.add_argument("--reports", type=Path, default=ROOT / "reports")
    args = parser.parse_args(argv)

    log_path = setup_logging("validate")
    log.info("logging to %s", log_path)
    try:
        params = load_params(args.params)
        frames = data_io.load_raw(args.raw, params["commit"], text=False)
        return run(frames, args.reports, params["iqr_factor"], params["min_rows"])
    except (data_io.MissingTable, ValueError, OSError, yaml.YAMLError) as error:
        log.error("validate failed: %s", error)
        return 1
    except Exception:
        log.exception("validate failed with an unexpected error")
        return 1


if __name__ == "__main__":
    sys.exit(main())
