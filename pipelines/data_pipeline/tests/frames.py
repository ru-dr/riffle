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
