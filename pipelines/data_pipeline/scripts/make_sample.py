"""Make a small, repeatable sample of the features table, to try the pipeline in seconds.

Run from pipelines/data_pipeline:  python -m scripts.make_sample

The sample is a standalone dataset tracked by DVC with `dvc add` (data/sample/features.parquet.dvc),
not a pipeline stage, so it only changes when someone chooses to remake it. It is a random draw of
rows from data/processed/features.parquet with a fixed seed, kept in time order, with each row's
train or test label unchanged. It has no usernames and no text, only the model's inputs and the
merged label, so it is as safe to share as the features table itself.

Try it:  python -m scripts.slices --processed data/sample --reports reports_sample
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

from scripts.logging_setup import ROOT, setup_logging

log = logging.getLogger("make_sample")

DEFAULT_ROWS = 20_000
DEFAULT_SEED = 0


def make_sample(features: pd.DataFrame, rows: int, seed: int) -> pd.DataFrame:
    """A random sample of `rows` rows in time order; the whole table if it is smaller."""
    if rows < 1:
        raise ValueError("rows must be at least 1")
    sample = features if rows >= len(features) else features.sample(n=rows, random_state=seed)
    return sample.sort_values("created_at").reset_index(drop=True)


def run(processed: Path, out: Path, rows: int, seed: int) -> Path:
    source = processed / "features.parquet"
    if not source.is_file():
        raise FileNotFoundError(f"{source} does not exist; run the features stage first")
    sample = make_sample(pd.read_parquet(source), rows, seed)
    out.mkdir(parents=True, exist_ok=True)
    target = out / "features.parquet"
    sample.to_parquet(target, index=False)
    log.info("sample: %d rows (%d test) written to %s", len(sample), int((sample["split"] == "test").sum()), target)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts.make_sample", description=__doc__.splitlines()[0])
    parser.add_argument("--processed", type=Path, default=ROOT / "data" / "processed")
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "sample")
    parser.add_argument("--rows", type=int, default=DEFAULT_ROWS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args(argv)

    log_path = setup_logging("make_sample")
    log.info("logging to %s", log_path)
    try:
        run(args.processed, args.out, args.rows, args.seed)
        return 0
    except (FileNotFoundError, ValueError, KeyError, OSError) as error:
        log.error("make_sample failed: %s", error)
        return 1
    except Exception:
        log.exception("make_sample failed with an unexpected error")
        return 1


if __name__ == "__main__":
    sys.exit(main())
