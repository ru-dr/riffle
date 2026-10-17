# 0009: Merge-outcome target beside label_bad

- **Date:** 2026-10-16
- **Status:** Accepted
- **Owners:** #2 Training, with #5 Data ingestion

## Context

ADR 0002 defines one target, `label_bad`. It asks whether a merged PR broke after merge, so it is trained on merged PRs only. Riffle also needs to know whether a PR will merge at all: a PR that will be closed should not take a senior reviewer's time. The repo miner kept only merged PRs, so it could not learn this. The AIDev feature catalog lists merge-outcome labels (merged vs closed, rework, duplicate, unwanted, abandoned, CI or technical) built from the PR timeline, reviews and comments. These come from the same GitHub data the miner already reads.

## Decision

- Add a second target, `label_merged`, trained on every closed PR, merged or not. `label_bad` keeps its ADR 0002 definition and stays conditional on merge. The two are never combined into one label.
- The scorer reports both. A combined risk, if wanted, is `P(merge) x P(bad | merged)`.
- Labels, all outcomes (never model inputs), null when not observed:

| Column | Rows | Rule |
| --- | --- | --- |
| `label_merged` | all | `merged_at` present. `state` is not used, because `closed` covers merged PRs too |
| `label_rework` | merged | A human non-author `CHANGES_REQUESTED` review, or a commit or force-push more than `rework_grace_seconds` after open |
| `label_duplicate` | unmerged | `marked_as_duplicate` event, a `duplicate` label, or a comment such as "duplicate of", "superseded by", "already fixed in" |
| `label_unwanted` | unmerged | With the LLM on: the close-reason classifier says `unwanted`. Without it: a maintainer (`OWNER`, `MEMBER`, `COLLABORATOR`, not the author) comment saying out of scope or won't fix, or an `unwanted_labels` label at close |
| `label_abandoned` | unmerged | No human non-author comment or review before close, closed by a stale bot, or still open after `abandon_days` (60) with no human non-author response |
| `label_ci_technical` | unmerged | Required checks fail on the head commit at close, with no maintainer change request, and not a duplicate |
| `close_reason` | unmerged | With the LLM on: a classifier reads the title, comments, reviews, labels, renames and who closed it, up to the close, and picks `duplicate`, `unwanted`, `ci_technical`, `abandoned` or `other`. Without it, or for a PR with no human activity: the first rule that fires, in that order, else `other`. `close_reason_source` says which |

- The classifier also adds to the rule flags: its `duplicate`, `abandoned` and `ci_technical` answers set those flags too, and a `marked_as_duplicate` event always counts. It runs only on the miner's training rows, never on the scoring path.
- Also written, for analysis only: `post_open_commits`, `post_open_force_pushes`, `changes_requested_reviews` (the counts behind `label_rework`), `created_at`, `closed_at`.
- Events after the close (plus one minute, for the closing comment and label) are ignored.
- Rows for unmerged PRs carry the same features as merged rows, computed at the PR's fork point from its base. Their post-merge columns (`label_bad` and its sources, `days_to_*`) are null. They never count as past PRs in the repo-local history features, which are built from merged PRs only.
- Unmerged PRs are taken from the same creation window as the merged PRs mined, so the mix of merged and unmerged rows is the repository's real one.
- Repositories mined in landed-commit mode get no unmerged rows, because there a closed PR may have landed. Landed-commit mode needs both a low GitHub-merge share and most closed PRs linking to commits on the default branch: a repository flooded with rejected PRs also has a low merge share.
- PRs still open after `abandon_days` with no human response are mined as unmerged and abandoned, with `closed_at` null. Open PRs that did get a response have no outcome yet and are left out. Drafts are left out.
- Settings: `labels.merge_outcome` in `contracts/org_rules.schema.json` (version 5.4): `enabled` (default `true`), `rework_grace_seconds` (300), `abandon_days` (60), `stale_bot_logins`, `unwanted_labels`.
- Miner: on by default in `pipelines/ingestion/repo_miner` from v2.0.0, so one run feeds both models. `--no-merge-outcome` (or unticking it in the TUI) gives the earlier output: merged PRs and `label_bad` only.

## Options not taken

| Option | Why not |
| --- | --- |
| One multiclass target (clean, bad, not merged) | Mixes two questions with different maturity: merge is known at close, breakage 30 to 90 days after merge |
| Fold not-merged into `label_bad` | A rejected PR is not a broken change; it would teach the model that unwanted work is dangerous |
| Use `state` for merged vs closed | `closed` covers merged PRs too |
| Fetch unmerged PR branches as local refs | `git log --all` scans in the miner would then see unmerged commits as history and as revert or hotfix candidates |
| Train a supervised unwanted-PR classifier first | Needs a labelled set we do not have yet; the LLM classifier works now and the hand-coded set can validate it |
| Treat every silent close as abandoned | A maintainer closing and retitling a PR "AI spam" in seconds is a rejection, not abandonment |

## Consequences

- One or two extra timeline calls per PR, one check-runs call per unmerged PR, and a fetch of each unmerged PR's commits. In one pilot repository this doubled run time and API calls; the share of unmerged PRs varies by repository.
- Every shard machine must mine with the same setting, or the merge model sees only some repositories. Repositories mined before v2.0.0 need a re-mine for merge labels.
- Unmerged rows run every per-row stage, the LLM flags included, so no feature is null only because a PR did not merge; that pattern would reveal `label_merged`. Scanner and LLM cost grow with the row count.
- Concurrency features (`concurrent_open_prs_same_*`) count unmerged PRs when the flag is on, which is closer to what was actually open.
- Features are computed from the PR as it opened ([ADR 0010](0010-features-at-open.md)), so commits pushed after open do not leak into either target.
- With the LLM on, one classifier call per unmerged PR that had human activity.
- `score_result.schema.json`, when written, needs a merge score beside the risk score.
- Training data for both targets comes from the miner's own scratch mining of the corpus. AIDev is not ingested.
- No conflict with the invariants. Labels are outcomes and never model inputs, and features stay point-in-time (ADR 0010).

## Revisit if

- A hand-coded sample shows `label_unwanted` or `label_duplicate` precision too low to train on.
- `close_reason = other` is the largest class in most repositories.
- Required-check results have expired for most unmerged PRs, leaving `label_ci_technical` mostly null.

## Open

- Validate the classifier against the 600 hand-coded PRs in Ehsani et al.
- Still-open abandoned rows can later merge; whether to re-mine them before training.
- Maturity: whether recently closed PRs need a waiting period before their labels are used.
- Whether the global model trains `label_merged` on agent and human PRs together, with `ai_agent_authored` as a feature, or separately.
