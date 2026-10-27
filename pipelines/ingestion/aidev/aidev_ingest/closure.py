"""A sanity check on the AIDev-pop tables: do their rows point at real PRs?

The dataset card says these tables cover AIDev-pop only. If a child table held a
`pr_id` that is not in `pull_request`, that claim (or our understanding of it)
would be wrong, and a join would silently drop rows. This reports; it never stops
a download.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

# Pop-scoped tables that carry a pr_id. pr_review_comments joins through a review id and
# issue through related_issue, so they are not covered here.
CHILDREN_WITH_PR_ID = ("pr_timeline", "pr_reviews", "pr_comments", "pr_commits", "related_issue")
SAMPLE = 5


def pop_closure(rev_dir: Path) -> dict[str, Any]:
    parent = rev_dir / "pull_request.parquet"
    if not parent.is_file():
        return {"checked": False, "reason": "pull_request.parquet is not present", "tables": {}}

    ids = pq.read_table(parent, columns=["id"]).column("id")
    results: dict[str, Any] = {}
    for name in CHILDREN_WITH_PR_ID:
        path = rev_dir / f"{name}.parquet"
        if not path.is_file():
            results[name] = {"checked": False}
            continue
        column = pq.read_table(path, columns=["pr_id"]).column("pr_id")
        if column.type != ids.type:
            column = column.cast(ids.type)
        found = pc.is_in(column, value_set=ids.combine_chunks() if isinstance(ids, pa.ChunkedArray) else ids)
        known = pc.sum(found.cast(pa.int64())).as_py() or 0
        nulls = column.null_count
        missing = len(column) - nulls - known
        entry: dict[str, Any] = {"checked": True, "rows": len(column), "null_pr_id": nulls, "not_in_pull_request": missing}
        if missing:
            unknown = pc.filter(column, pc.invert(found))
            entry["sample_ids"] = [v for v in unknown.drop_null().slice(0, SAMPLE).to_pylist()]
        results[name] = entry
    return {"checked": True, "tables": results}


def format_report(report: dict[str, Any]) -> str:
    if not report["checked"]:
        return f"pop-closure: not checked ({report['reason']})"
    lines = ["pop-closure: rows whose pr_id is not in pull_request"]
    for name, entry in report["tables"].items():
        if not entry["checked"]:
            lines.append(f"  {name:<20} not present")
        else:
            lines.append(
                f"  {name:<20} {entry['not_in_pull_request']:>10,} of {entry['rows']:>10,} rows"
                f" ({entry['null_pr_id']:,} null pr_id)"
            )
    return "\n".join(lines)
