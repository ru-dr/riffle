# 0026: Rescore open PRs when a model is promoted

- **Date:** 2026-11-25
- **Status:** Proposed
- **Owners:** #9 Inference, with #8 GitHub App (to confirm, see Open)

## Context

`rescore` in `contracts/org_config.schema.json` triggers rescores only from PR events (`push`, `ready_for_review`, `ci_completed`, `reopened`, `edited`, `labeled`), with at most `max_rescores_per_pr` (default 20) per PR. A promoted global model or per-repo layer (ADR 0001, and the promotion gate in ADR 0014) changes `model_version`, but an open PR keeps its score from the retired version until something happens on the PR. Scoring is idempotent per `delivery_id`, and a rescore caused by a promotion has no GitHub delivery. The `PrEvent` actions are listed in the README; `contracts/pr_event.schema.json` is not written yet. ADR 0020 (Proposed) refreshes the rank in the PR comment when a PR is rescored.

## Decision

- **Which PRs are rescored:**
  - When a global model is promoted, every tenant's open PRs are queued for one rescore.
  - When a per-repo layer is promoted, that tenant's open PRs are.
- **New `PrEvent` action `model_promoted`.**
- **Each rescore carries a synthetic delivery ID,** `rescore:<tenant_id>:<repo>:<pr_number>:<head_sha>:<model_version>`. The existing idempotency per `delivery_id` covers it, so there is still one rule: a PR is rescored at most once per head and promoted version.
- **Rescores go to a lower-priority lane of the message queue,** below live PR events and above history backfill.
- **They do not count towards `max_rescores_per_pr`.** A busy PR still gets every promoted model.
- **The rescore reuses the PR's at-open features (ADR 0010).** The new model sees the same kind of input it was trained on; only `model_version` changes.
- **To limit churn, the band shown on GitHub changes only if the new score crosses a band boundary by more than a margin.** The dashboard always shows the latest score.
- **Not a setting:** it is not added to `rescore.on`, because promotion is not a PR event and a stale `model_version` should not be something a repo can opt into.

## Options not taken

| Option | Why not |
| --- | --- |
| Do not rescore on promotion | Open PRs keep scores from a retired model until the next push |
| Rescore only when someone opens the PR on the dashboard | The band and comment on GitHub stay stale |
| Rescore closed PRs too | No one acts on them; it spends compute and GitHub API budget for nothing |
| A second idempotency key just for rescores | Two rules where one will do; the synthetic delivery ID fits the existing rule |
| Count promotion rescores against `max_rescores_per_pr` | A PR that already hit the cap would never get a newer model |

## Consequences

- **The `PrEvent` action list** in the README gains `model_promoted`, and `contracts/pr_event.schema.json` includes it when it is written. Adding the action is a contract change: it needs a version bump and approval from every affected owner, in the same PR as the code.
- **The registry publishes a promotion event,** and a job turns it into one `PrEvent` per open PR.
- **Load:** each promotion sends a burst of rescores, sized by the number of open PRs. The low-priority lane absorbs it. Check-run updates go through the GitHub API's per-installation rate limit like every other call.
- **The score log** (ADR 0024) records these rows with `trigger` = `model_promoted`.
- **Invariants:**
  - **One pinned `model_version` per request** still holds: each rescore pins the promoted version.
  - **Fail open:** a failed rescore leaves the previous score in place.

## Revisit if

- Promotions happen often enough that rescores delay live PR events.
- Reviewers report that the queue reorders too often.
- `contracts/pr_event.schema.json` is written in a way that conflicts with a synthetic delivery ID.

## Open

- The margin a score must cross before the band on GitHub changes.
- Whether draft PRs are rescored.
- Owners: #9 Inference and #8 GitHub App are a proposal; confirm them.
