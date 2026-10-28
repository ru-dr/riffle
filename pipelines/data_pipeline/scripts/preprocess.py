"""Stage 3, preprocess: clean the raw tables and join them into one row per pull request.

Run from pipelines/data_pipeline:  python -m scripts.preprocess

Outputs in data/processed/ and reports/:
  pull_requests_clean.parquet  one row per pull request with its repository's columns
  quarantine.parquet           rows set aside, each with the reason (ids and dates only)
  preprocess.json              counts in, out and per reason

Nothing is silently dropped: a row either lands in the clean table or in quarantine with a
reason. Nulls stay null (an open pull request has no closed_at); filling them for a model is
the features stage's job. Usernames and URLs are not carried forward, and neither is the text
of titles and bodies, only their lengths, so free text stays in the raw layer only.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from scripts import anomalies, data_io
from scripts.logging_setup import ROOT, setup_logging

log = logging.getLogger("preprocess")

TIME_COLUMNS = ("created_at", "closed_at", "merged_at")
REPO_COLUMNS = {"id": "repo_id", "full_name": "repo_name", "language": "language", "license": "license",
                "is_forked": "is_forked", "forks": "forks", "stars": "stars"}
CLEAN_COLUMNS = ["id", "number", "repo_id", "repo_name", "agent", "state", "title_chars", "title_words",
                 "body_chars", *TIME_COLUMNS, "language", "license", "is_forked", "forks", "stars"]
QUARANTINE_COLUMNS = ["id", "number", "repo_id", "state", *TIME_COLUMNS, "reason"]


def quarantine_reasons(pr: pd.DataFrame, repo: pd.DataFrame) -> pd.Series:
    """For each pull request the reasons to set it aside, joined by '; ' (empty when none)."""
    masks = anomalies.contradiction_masks(pr)
    masks["duplicate of an earlier pull request"] = pr.duplicated(["repo_id", "number"], keep="first")
    masks["repository not found"] = ~pr["repo_id"].isin(repo["id"])
    reasons = pd.Series("", index=pr.index, dtype="object")
    for message, mask in masks.items():
        # Rows that break this rule get the message appended after any earlier one.
        separator = reasons.where(reasons == "", "; ")
        reasons = reasons.where(~mask, reasons + separator + message)
    return reasons


def clean(frames: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (clean, quarantine). Together they hold every input pull request exactly once."""
    pr, repo = frames["pull_request"].copy(), frames["repository"]
    reasons = quarantine_reasons(pr, repo)
    bad = reasons != ""

    quarantine = pr.loc[bad, [c for c in QUARANTINE_COLUMNS if c != "reason"]].assign(reason=reasons[bad])
    quarantine = quarantine[QUARANTINE_COLUMNS].reset_index(drop=True)

    good = pr.loc[~bad].copy()
    for column in TIME_COLUMNS:
        good[column] = pd.to_datetime(good[column], utc=True)
    # Lengths, not text. A missing body is 0 characters; "no body" and "an empty body" are the same to a model.
    title = good["title"].fillna("").str.strip()
    good["title_chars"] = title.str.len()
    good["title_words"] = title.str.split().str.len()
    good["body_chars"] = good["body"].fillna("").str.len()
    repo_part = repo[list(REPO_COLUMNS)].rename(columns=REPO_COLUMNS)
    joined = good.merge(repo_part, on="repo_id", how="left", validate="many_to_one")
    return joined[CLEAN_COLUMNS].reset_index(drop=True), quarantine


def summary(rows_in: int, clean_df: pd.DataFrame, quarantine: pd.DataFrame) -> dict[str, Any]:
    by_reason: dict[str, int] = {}
    for text in quarantine["reason"]:
        for reason in text.split("; "):
            by_reason[reason] = by_reason.get(reason, 0) + 1
    return {
        "rows_in": rows_in,
        "rows_clean": int(len(clean_df)),
        "rows_quarantined": int(len(quarantine)),
        "quarantined_by_reason": by_reason,
    }


def run(frames: dict[str, pd.DataFrame], processed: Path, reports: Path) -> dict[str, Any]:
    clean_df, quarantine = clean(frames)
    result = summary(len(frames["pull_request"]), clean_df, quarantine)
    if result["rows_clean"] + result["rows_quarantined"] != result["rows_in"]:
        raise RuntimeError("rows were lost: clean plus quarantine does not equal the input")
    processed.mkdir(parents=True, exist_ok=True)
    reports.mkdir(parents=True, exist_ok=True)
    clean_df.to_parquet(processed / "pull_requests_clean.parquet", index=False)
    quarantine.to_parquet(processed / "quarantine.parquet", index=False)
    (reports / "preprocess.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    log.info("preprocess: %d in, %d clean, %d quarantined", result["rows_in"], result["rows_clean"], result["rows_quarantined"])
    for reason, count in result["quarantined_by_reason"].items():
        log.warning("quarantined %d: %s", count, reason)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts.preprocess", description=__doc__.splitlines()[0])
    parser.add_argument("--params", type=Path, default=ROOT / "params.yaml")
    parser.add_argument("--raw", type=Path, default=ROOT / "data" / "raw")
    parser.add_argument("--processed", type=Path, default=ROOT / "data" / "processed")
    parser.add_argument("--reports", type=Path, default=ROOT / "reports")
    args = parser.parse_args(argv)

    log_path = setup_logging("preprocess")
    log.info("logging to %s", log_path)
    try:
        document = yaml.safe_load(args.params.read_text(encoding="utf-8")) or {}
        commit = (document.get("aidev") or {}).get("commit")
        if not commit:
            raise ValueError(f"{args.params} has no aidev.commit")
        run(data_io.load_raw(args.raw, commit), args.processed, args.reports)
        return 0
    except (data_io.MissingTable, ValueError, KeyError, OSError, yaml.YAMLError) as error:
        log.error("preprocess failed: %s", error)
        return 1
    except Exception:
        log.exception("preprocess failed with an unexpected error")
        return 1


if __name__ == "__main__":
    sys.exit(main())
