"""What the two AIDev tables must look like, written down once.

The numbers come from profiling the pinned revision (see README, "What the checks are
based on"). Null rates have a margin so a new revision with slightly different data does not
raise a false alarm, while a real break (a column emptied, a format changed) still does.
"""

from __future__ import annotations

# Column name -> kind. Kinds are checked with pandas' own type tests, not exact dtype strings,
# so the same schema holds for pandas 2 and 3.
PULL_REQUEST = {
    "id": "int",
    "number": "int",
    "title": "str",
    "body": "str",
    "agent": "str",
    "user_id": "int",
    "user": "str",
    "state": "str",
    "created_at": "str",
    "closed_at": "str",
    "merged_at": "str",
    "repo_id": "int",
    "repo_url": "str",
    "html_url": "str",
}
REPOSITORY = {
    "id": "int",
    "url": "str",
    "license": "str",
    "full_name": "str",
    "is_forked": "bool",
    "language": "str",
    "forks": "int",
    "stars": "int",
}
SCHEMAS = {"pull_request": PULL_REQUEST, "repository": REPOSITORY}

# Highest share of nulls tolerated per column; a column not listed must have none. Null is
# meaningful for closed_at (an open PR) and merged_at (an unmerged one), so any share is allowed
# there and anomalies.consistency() checks that they agree with `state`.
MAX_NULL_RATE = {
    "pull_request": {"body": 0.02, "closed_at": 1.0, "merged_at": 1.0},
    "repository": {"license": 0.15, "language": 0.05},
}

AGENTS = ["Claude_Code", "Copilot", "OpenAI_Codex", "Cursor", "Devin", "Google_Jules"]
STATES = ["open", "closed"]

TIMESTAMP_REGEX = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"
PR_URL_REGEX = r"^https://github[.]com/[^/]+/[^/]+/pull/[0-9]+$"
FULL_NAME_REGEX = r"^[^/]+/[^/]+$"
