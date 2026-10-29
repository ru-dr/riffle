"""Small valid tables for the tests; each test breaks one thing."""

from __future__ import annotations

import pandas as pd

from scripts import schema


def good_repository(rows: int = 30) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "id": range(1, rows + 1),
            "url": [f"https://api.github.com/repos/o/r{i}" for i in range(rows)],
            "license": ["MIT"] * rows,
            "full_name": [f"o/r{i}" for i in range(rows)],
            "is_forked": [False] * rows,
            "language": ["Python"] * rows,
            "forks": [i % 7 for i in range(rows)],
            "stars": [100 + i * 3 for i in range(rows)],
        }
    )


def good_pull_request(rows: int = 30) -> pd.DataFrame:
    agents = schema.AGENTS
    closed = [i % 4 != 0 for i in range(rows)]  # a quarter stay open
    merged = [i % 4 != 0 and i % 3 != 0 for i in range(rows)]
    return pd.DataFrame(
        {
            "id": range(1000, 1000 + rows),
            "number": range(1, rows + 1),
            "title": [f"change {i}" for i in range(rows)],
            "body": [f"body {i}" for i in range(rows)],
            "agent": [agents[i % len(agents)] for i in range(rows)],
            "user_id": [7] * rows,
            "user": ["bot"] * rows,
            "state": ["closed" if c else "open" for c in closed],
            "created_at": [f"2025-01-{1 + i % 28:02d}T10:00:00Z" for i in range(rows)],
            "closed_at": [f"2025-02-{1 + i % 28:02d}T10:00:00Z" if c else None for i, c in enumerate(closed)],
            "merged_at": [f"2025-02-{1 + i % 28:02d}T10:00:00Z" if m else None for i, m in enumerate(merged)],
            "repo_id": [1 + i % 30 for i in range(rows)],
            "repo_url": ["https://api.github.com/repos/o/r"] * rows,
            "html_url": [f"https://github.com/o/r/pull/{i + 1}" for i in range(rows)],
        }
    )


def good_frames() -> dict[str, pd.DataFrame]:
    return {"pull_request": good_pull_request(), "repository": good_repository()}


def synthetic_features(rows: int = 3000, seed: int = 0) -> pd.DataFrame:
    """A features table (same columns as the features stage writes) with a planted disparity.

    Agent "Beta" merges less often than "Alpha" in the first 80 % of time (the training period), so
    a model learns to distrust it. In the last 20 % the two merge equally often, which is the kind
    of drift the real data has.
    """
    import numpy as np

    from scripts import features as feature_stage

    rng = np.random.default_rng(seed)
    created = pd.date_range("2025-01-01", periods=rows, freq="h", tz="UTC")
    train = np.arange(rows) < int(rows * 0.8)
    agent = rng.choice(["Alpha", "Beta", "Gamma"], rows, p=[0.5, 0.3, 0.2])
    log_stars = rng.normal(6, 1.2, rows)
    logit = 0.9 * (log_stars - 6) + 1.0 - np.where((agent == "Beta") & train, 1.2, 0.0)
    merged = (rng.random(rows) < 1 / (1 + np.exp(-logit))).astype(int)
    stars = np.expm1(log_stars).round().astype(int)
    languages = rng.choice(["Python", "Go", "Rust", "unknown"], rows)
    out = pd.DataFrame(
        {
            "id": np.arange(rows),
            "repo_id": rng.integers(1, 200, rows),
            "created_at": created,
            "split": np.where(train, "train", "test"),
            "merged": merged,
            "agent": agent,
            "language": languages,
            "license": "MIT",
            "is_forked": rng.integers(0, 2, rows),
            "stars": stars,
            "forks": (stars // 10).astype(int),
            "log_stars": np.log1p(stars),
            "log_forks": np.log1p(stars // 10),
            "pr_number": rng.integers(1, 500, rows),
            "title_chars": rng.integers(5, 90, rows),
            "title_words": rng.integers(1, 15, rows),
            "body_chars": rng.integers(0, 3000, rows),
            "has_body": rng.integers(0, 2, rows),
            "hour_utc": created.hour,
            "weekday": created.day_of_week,
            "is_weekend": (created.day_of_week >= 5).astype(int),
            "stars_bucket": pd.cut(stars, feature_stage.STAR_BINS, labels=feature_stage.STAR_LABELS, right=False).astype(str),
            "license_family": rng.choice(["permissive", "copyleft", "unknown"], rows),
        }
    )
    return out[feature_stage.OUTPUT_COLUMNS]
