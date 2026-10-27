"""Stage 4, features: one row per closed pull request, with the label and creation-time features.

Run from pipelines/data_pipeline:  python -m scripts.features

The label `merged` is 1 when the pull request was merged and 0 when it was closed without a
merge. Open pull requests are left out: their outcome is not known yet, and a missing outcome
is not the same as "not merged".

Only what was known when the pull request was opened becomes a feature. Anything from after
that (closed_at, how long it stayed open) would leak the answer, so it is not here. The
train/test split is by time: the newest `test_fraction` of pull requests are the test set, so
a model is never scored on the past while having seen the future.

The last columns (`stars_bucket`, `license_family`) exist to slice results by group in the
bias analysis; they are derived from the same features.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from scripts.logging_setup import ROOT, setup_logging

log = logging.getLogger("features")

STAR_BINS = [0, 200, 1_000, 5_000, np.inf]
STAR_LABELS = ["100-199", "200-999", "1k-4.9k", "5k+"]  # AIDev-pop repositories have more than 100 stars
PERMISSIVE = ("mit", "apache", "bsd", "isc", "unlicense", "cc0", "zlib", "0bsd")
COPYLEFT = ("gpl", "agpl", "lgpl", "mpl", "epl", "eupl", "cc-by-sa")

FEATURE_COLUMNS = [
    "agent", "language", "license", "is_forked", "stars", "forks", "log_stars", "log_forks",
    "pr_number", "title_chars", "title_words", "body_chars", "has_body", "hour_utc", "weekday", "is_weekend",
]
OUTPUT_COLUMNS = ["id", "repo_id", "created_at", "split", "merged", *FEATURE_COLUMNS, "stars_bucket", "license_family"]


def license_family(value: Any) -> str:
    """permissive | copyleft | other | unknown. GitHub's NOASSERTION means no licence was detected."""
    if value is None or pd.isna(value) or str(value).upper() in ("NOASSERTION", "OTHER"):
        return "unknown"
    text = str(value).lower()
    if any(text.startswith(p) for p in COPYLEFT):
        return "copyleft"
    if any(text.startswith(p) for p in PERMISSIVE):
        return "permissive"
    return "other"


def build(clean: pd.DataFrame, test_fraction: float) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Return the feature table and a summary (counts, split cutoff, label balance)."""
    closed = clean[clean["state"] == "closed"].copy()
    out = pd.DataFrame(index=closed.index)
    out["id"], out["repo_id"], out["created_at"] = closed["id"], closed["repo_id"], closed["created_at"]
    out["merged"] = closed["merged_at"].notna().astype(int)

    out["agent"] = closed["agent"]
    out["language"] = closed["language"].fillna("unknown")
    out["license"] = closed["license"].fillna("unknown")
    out["is_forked"] = closed["is_forked"].astype(int)
    out["stars"], out["forks"] = closed["stars"], closed["forks"]
    out["log_stars"], out["log_forks"] = np.log1p(closed["stars"]), np.log1p(closed["forks"])

    title, body = closed["title"].fillna(""), closed["body"].fillna("")
    out["pr_number"] = closed["number"]
    out["title_chars"], out["title_words"] = title.str.len(), title.str.split().str.len()
    out["body_chars"] = body.str.len()
    out["has_body"] = (out["body_chars"] > 0).astype(int)
    out["hour_utc"] = closed["created_at"].dt.hour
    out["weekday"] = closed["created_at"].dt.day_of_week
    out["is_weekend"] = (out["weekday"] >= 5).astype(int)

    out["stars_bucket"] = pd.cut(closed["stars"], STAR_BINS, labels=STAR_LABELS, right=False).astype(str)
    out["license_family"] = closed["license"].map(license_family)

    cutoff = None
    out["split"] = "train"
    if len(out):
        cutoff = out["created_at"].quantile(1 - test_fraction)
        out.loc[out["created_at"] > cutoff, "split"] = "test"
    out = out.sort_values("created_at").reset_index(drop=True)[OUTPUT_COLUMNS]

    summary = {
        "rows": int(len(out)),
        "open_excluded": int((clean["state"] != "closed").sum()),
        "train": int((out["split"] == "train").sum()),
        "test": int((out["split"] == "test").sum()),
        "split_cutoff": None if cutoff is None else cutoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "merged_rate": None if out.empty else round(float(out["merged"].mean()), 4),
    }
    return out, summary


def run(processed: Path, reports: Path, test_fraction: float) -> dict[str, Any]:
    path = processed / "pull_requests_clean.parquet"
    if not path.is_file():
        raise FileNotFoundError(f"{path} does not exist; run the preprocess stage first")
    features, summary = build(pd.read_parquet(path), test_fraction)
    features.to_parquet(processed / "features.parquet", index=False)
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "features.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    log.info("features: %d rows (%d train, %d test), merged rate %s, cutoff %s", summary["rows"], summary["train"],
             summary["test"], summary["merged_rate"], summary["split_cutoff"])
    return summary


def load_test_fraction(path: Path) -> float:
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    value = (document.get("features") or {}).get("test_fraction", 0.2)
    if not isinstance(value, (int, float)) or not 0 < value < 1:
        raise ValueError("features.test_fraction must be a number between 0 and 1")
    return float(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts.features", description=__doc__.splitlines()[0])
    parser.add_argument("--params", type=Path, default=ROOT / "params.yaml")
    parser.add_argument("--processed", type=Path, default=ROOT / "data" / "processed")
    parser.add_argument("--reports", type=Path, default=ROOT / "reports")
    args = parser.parse_args(argv)

    log_path = setup_logging("features")
    log.info("logging to %s", log_path)
    try:
        run(args.processed, args.reports, load_test_fraction(args.params))
        return 0
    except (FileNotFoundError, ValueError, KeyError, OSError, yaml.YAMLError) as error:
        log.error("features failed: %s", error)
        return 1
    except Exception:
        log.exception("features failed with an unexpected error")
        return 1


if __name__ == "__main__":
    sys.exit(main())
