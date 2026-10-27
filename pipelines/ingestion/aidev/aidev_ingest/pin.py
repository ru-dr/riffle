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

FETCH_POLICIES = ("as_is", "pruned", "skip")
SCOPES = ("pop", "all")


@dataclass(frozen=True)
class TablePlan:
    """How a table is fetched into the raw layer.

    fetch:
      as_is   download the file byte for byte and check it against its checksum
      pruned  read in pieces and write a new file without `drop_columns`
      skip    not fetched
    scope (from the dataset card, which lists which tables cover what):
      pop     only AIDev-pop, repositories with more than 100 stars
      all     every repository in AIDev
    drop_columns are removed before writing (patches carry licence risk).
    hold_upload, when set, is why the table must not be uploaded yet; `upload` skips it
    unless it is named in --allow-held. It is a stop sign for a decision, not a ban.
    """

    fetch: str
    reason: str
    drop_columns: tuple[str, ...] = ()
    scope: str = "pop"
    hold_upload: str = ""


TABLES: dict[str, TablePlan] = {
    "pull_request": TablePlan("as_is", "AIDev-pop PRs (361k); backbone of the unified table"),
    "repository": TablePlan("as_is", "stars, language and licence for the pop repos"),
    "all_repository": TablePlan(
        "as_is", "covers pop-sized repos missing from `repository`; has is_forked", scope="all"
    ),
    "all_user": TablePlan("as_is", "author accounts; real names stay in raw only", scope="all"),
    "pr_timeline": TablePlan("as_is", "events: duplicates, labels, merger"),
    "pr_reviews": TablePlan("as_is", "CHANGES_REQUESTED and non-author responses (labels)"),
    "pr_comments": TablePlan("as_is", "maintainer comments for the duplicate and unwanted classes"),
    "pr_review_comments": TablePlan(
        "as_is",
        "inline review comments; joins via pull_request_review_id",
        hold_upload=(
            "its diff_hunk column holds source-code snippets from repositories with mixed licences; "
            "decide whether to upload it as is, drop that column, or leave it local"
        ),
    ),
    "pr_commits": TablePlan("as_is", "commits after open mean rework"),
    "issue": TablePlan("as_is", "linked issue text, an open-time feature"),
    "related_issue": TablePlan("as_is", "PR to issue links"),
    "pr_commit_details": TablePlan(
        "pruned",
        "per-file diff stats, the only source of PR-level size. AIDev-pop only already, so no row "
        "filter is needed; only the `patch` column is dropped (the table is 12.4 GB)",
        drop_columns=("patch",),
    ),
    "all_pull_request": TablePlan(
        "skip", "PRs from every repository (7.7M, 6.7 GB); we use the AIDev-pop subset", scope="all"
    ),
}


def planned_tables() -> list[str]:
    return sorted(name for name, plan in TABLES.items() if plan.fetch != "skip")


def as_is_tables() -> list[str]:
    return sorted(name for name, plan in TABLES.items() if plan.fetch == "as_is")
