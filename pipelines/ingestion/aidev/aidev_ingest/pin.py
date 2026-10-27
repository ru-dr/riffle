"""What we ingest from AIDev, and exactly which revision.

The revision is a commit hash, not a tag or branch. `main` and the tags can move
(`main` already differs from tag `v5` by hash while holding the same data), and a
pinned hash is the only thing that lets a later run prove it read the same bytes.
"""

from __future__ import annotations

from dataclasses import dataclass

REPO_ID = "hao-li/AIDev"
REPO_TYPE = "dataset"

# Tag v5 (cutoff 2026-03-31) resolved to this commit when it was pinned.
TAG = "v5"
REVISION = "b37f9fd1613cfec55886105bb76721918d015dc7"


@dataclass(frozen=True)
class TablePlan:
    """How a table is fetched into the raw layer.

    fetch:
      full          take every row and column as published
      pop_filtered  keep only rows whose pr_id is in the AIDev-pop set
      skip          not fetched
    drop_columns are removed before writing (patches carry licence risk).
    """

    fetch: str
    reason: str
    drop_columns: tuple[str, ...] = ()


TABLES: dict[str, TablePlan] = {
    "pull_request": TablePlan("full", "AIDev-pop PRs (361k); backbone of the unified table"),
    "repository": TablePlan("full", "stars, language and licence for the pop repos"),
    "all_repository": TablePlan("full", "covers pop-sized repos missing from `repository`; has is_forked"),
    "all_user": TablePlan("full", "author accounts; real names stay in raw only"),
    "pr_timeline": TablePlan("full", "events: duplicates, labels, merger. Pop only already"),
    "pr_reviews": TablePlan("full", "CHANGES_REQUESTED and non-author responses (labels)"),
    "pr_comments": TablePlan("full", "maintainer comments for the duplicate and unwanted classes"),
    "pr_review_comments": TablePlan("full", "inline review comments; joins via pull_request_review_id"),
    "pr_commits": TablePlan("full", "commits after open mean rework"),
    "issue": TablePlan("full", "linked issue text, an open-time feature"),
    "related_issue": TablePlan("full", "PR to issue links"),
    "pr_commit_details": TablePlan(
        "pop_filtered",
        "per-file diff stats; the only source of PR-level size",
        drop_columns=("patch",),
    ),
    "all_pull_request": TablePlan("skip", "6.7 GB, 95% outside the pop set"),
}


def planned_tables() -> list[str]:
    return sorted(name for name, plan in TABLES.items() if plan.fetch != "skip")
