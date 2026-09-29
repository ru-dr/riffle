# 0003: Branch and merge rules

- **Date:** 2026-09-29
- **Status:** Proposed
- **Owners:** Whole team; enforced by #10 Deployment & CI/CD

## Context

The handbook sets trunk-based development with branches named
`<workstream>/<short-description>`, but work outside the four services (docs,
CI, repo hygiene, dependency bumps) had no agreed prefix. Branches such as
`repo-mining` already break the convention. Merge rules were spread across the
handbook and not enforced on GitHub. A team building a review router should
not have an unclear review process of its own.

## Decision

- Branch names are `<prefix>/<short-description>`, lowercase, words joined
  with hyphens.
- Product work uses an area prefix: `intake/`, `scorer/`, `explainer/`,
  `app/`, `pipelines/`, `infra/`, `site/`.
- Other work uses a type prefix: `docs/` (docs and ADRs only), `ci/` (GitHub
  Actions), `chore/` (repo hygiene, config, tooling), `fix/` (bug fixes that
  cross areas), `deps/` (manual dependency bumps). Dependabot keeps its own
  branch names.
- No direct pushes to `main`. Every change goes through a PR.
- CI must be green before merge.
- One approval from the named reviewer. If they have not responded within 24
  hours, any approval merges it.
- Changes to `contracts/` need approval from every affected owner, and the ADR
  if one applies.
- A PR that implements a decision needs its ADR (see the ADR rule in
  `docs/adr/PROMPT.md`).
- Squash merge only; branches are deleted after merge.
- Branches are short-lived. Rebase on `main` rather than merging `main` in.
- Never force-push a branch someone else has pushed to.
- Definition of done is unchanged from the handbook: tests pass in CI, it runs
  in staging, it emits a metric and a log line, and any contract or handbook
  section it touches is updated.
- Enforced by a GitHub ruleset on `main`: require PR, require passing CI,
  require one approval, squash merge only, delete branch on merge. Branch name
  patterns enforced by a repository ruleset where the plan allows it.

## Options not taken

| Option | Why not |
| --- | --- |
| Area prefixes only, as in the handbook | No home for docs, CI, and hygiene work |
| Type prefixes only (`feat/`, `fix/`, ...) | Loses which workstream owns the branch |
| Merge commits instead of squash | Noisy history; a PR's individual commits are not useful on `main` |
| Two required approvals | Too slow for a five-person team; the 24-hour rule covers absent reviewers |
| Rules in Discord only | Chat is ephemeral; rules belong in the repo and on GitHub |

## Consequences

- `repo-mining` is renamed `pipelines/repo-mining`.
- The handbook's working agreement section is updated to point here.
- #10 sets up the ruleset; until then the rules are followed by agreement.
- CI must exist for every area that has code, so "CI must be green" is real.

## Revisit if

- The 24-hour rule is used to merge more than a few PRs a week without review.
- Squash merge makes debugging a regression noticeably harder.
- The team grows past six people.
