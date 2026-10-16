# 0010: Diff features from the PR as it opened

- **Date:** 2026-10-16
- **Status:** Accepted
- **Owners:** #1 Feature engineering, with #5 Data ingestion

## Context

The scorer scores a PR when it opens, and `contracts/mined_features.schema.json` requires point-in-time inputs. The repo miner computed every diff feature from the PR's final state, so a PR that grew under review was measured at its merged size. Commits pushed after open are mostly rework, and rework is part of what the targets measure (ADR 0009's `label_rework`, and engagement that predicts `label_merged`), so the label leaked into the features. For squash and rebase merges the base was the merge commit's parent, so history after open leaked in too. GitHub's REST timeline does not record the head a force-push replaced; GraphQL's `HeadRefForcePushedEvent.beforeCommit` does, and GitHub serves that commit by SHA.

## Decision

- Every diff-derived feature (sizes, entropy, path flags, blame of modified lines, scanners, dependency and CVE checks, deterministic checks, API contract diffs, LLM flags, `path_size_pctile`, the files used by `similar_pr_*` and concurrency) is computed from the PR as it stood at open:
  - **head at open:** the newest commit on the PR branch's first-parent line committed at or before `created_at + rework_grace_seconds` (300 s, the same grace as `label_rework`). The walk stops at GitHub's recorded `base.sha`, so commits landed on the base branch with their original dates are never mistaken for it.
  - **force-pushed after open:** the walk starts from the `beforeCommit` of the first force-push after the grace period, fetched by SHA.
  - **base:** the merge-base of that head and `base.sha`. History features use this base, which is never after open.
- `base_behind_by` becomes how many base-branch commits the fork point was behind when the PR opened.
- PR metadata read from the PR object is today's, not the opening's. Where the timeline is fetched (always, with merge-outcome mining on), the title (`title_len`, and the `FIX` flag), the milestone (`has_milestone`) and the labels (`declared_hotfix`) are rebuilt as they were at open from `renamed`, `milestoned` and `labeled` events. Maintainers retitle rejected PRs ("AI spam") and milestone accepted ones, so today's values leak the outcome; in one pilot repository 210 of 312 titles had changed. `head_from_fork` is null when the head repository was deleted, instead of false.
- Post-merge labels keep the final range: revert, hotfix, CI and SZZ are about what landed.
- As history for later PRs, a merged PR is seen by its final diff, which is known once it merged.
- New column `open_snapshot`: `exact` (no force-push after open), `recovered` (walked from a replaced head), `approx` (force-push history unreadable), `final` (not recoverable; diff features use the final range and may leak). Training drops or masks diff features on `final` rows.
- Force-pushes come from one batched GraphQL query per 25 PRs (a separate rate budget from REST). PR heads not in the clone are fetched from `refs/pull/N/head` without local refs.
- Miner v2.0.0. The change is part of that major version.

## Options not taken

| Option | Why not |
| --- | --- |
| Keep final-state features | Leaks rework into both targets; offline scores overstate production |
| Two snapshots per row (open and final) | About twice the feature and LLM cost and twice the columns, for a breakage model scored at merge, which no service does yet |
| PR size from the API | The API gives only the current size, so the at-open state must be rebuilt from commits anyway |
| Base branch at open by commit date | Commits landed on main keep their original dates, so a date walk can land inside the PR |
| Null the diff features when not recoverable | Loses the row for the breakage model; the `open_snapshot` flag lets training choose |

## Consequences

- Feature values change for every PR that changed after opening. In two pilot repositories, 7 of 64 and 34 of 352 PRs were recovered from a force-push, and every exact head matched GitHub's own commit list (57 of 57).
- One GraphQL call per 25 PRs and a fetch of PR heads that squash and rebase merges rewrote. Feature compute is unchanged: a different commit is diffed, not an extra one.
- The PR body is still read at mining time and can have been edited after open.
- A commit made before open but pushed after it counts as part of the PR at open, because commit times are commit dates, not push times. The REST and GraphQL APIs do not expose push times for ordinary pushes.
- No conflict with the invariants; this removes a point-in-time violation.

## Revisit if

- `final` exceeds a few percent of rows in most repositories.
- GitHub stops serving force-pushed-over commits by SHA.

## Open

- Body at open, from GraphQL `userContentEdits`.
