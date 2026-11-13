# 0022: Point-in-time features and leak exclusions for the at-open outcome model

- **Date:** 2026-11-12
- **Status:** Proposed
- **Owners:** #2 Training

## Context

The outcome model predicts `outcome_3class` (ADR 0009) when a PR opens, from the miner's dataset table. Some columns
in that table are read at mining time, not at open, and carry the outcome: on the v0.1 team data (23 repos, 192,998
PRs), adding `author_association` back alone raises skill against the base rate from 0.280 to 0.397 (by PR) and
from 0.226 to 0.351 (by repo). The miner's own history columns cannot be checked for point-in-time correctness from
the table, and the model needs author and repo history. About 42% of rework PRs have a recovered (not exact) at-open
diff against almost no clean PRs, and a model can tell recovered from exact at AUROC 0.64 from which columns are
blank alone; in production the at-open diff is always exact. 1,339 rows have no `closed_at`: the miner's stale-open
rows, labelled abandoned at mining.

## Decision

- **Features live in `pipelines/training/outcome_features.py`:** 161 inputs, 131 miner columns
  (`MINER_FEATURES`) and 30 engineered (`ENGINEERED`), built by `build_features` from the whole dataset table.
- **Never inputs (`LEAKS`):** `author_association`; the body columns `body_len`, `body_headings`,
  `body_checklist_items`, `body_has_test_plan`, `has_linked_issue`, `agent_boilerplate`; `max_sim_open_prs` and
  `max_sim_recent_merged` (similarity on the mining-time body); `ai_agent_authored` and `agent_name` (partly body
  watermarks); `author_prs_merged_repo` (blank only on merged PRs).
- **History is point-in-time:** a past PR counts only if it closed strictly before this PR opened. A row with no
  `closed_at` never lends its outcome or an open or closed count to anyone. Unknown authors (no commit email) get no
  author history.
  - Author in this repo (`ah_*`): outcome counts, rates shrunk towards the repo's 365-day mix (strength 3), PRs open
    now, first PR, days since the previous PR, tenure.
  - Repo (`rh*`): the 90-day outcome mix (5 or more PRs, else null), PRs open now, PRs opened in 30 days, mean log
    hours to close, and how outside authors and outside first-timers fared over 180 days (shrunk, strength 5).
- **Consolidated columns (`c_*`):** first commit ever (`EXP == 0`), `SEXP / (EXP + 1)`, churn, share deleted, files
  per directory, and the 18 LLM detail answers compressed into `c_llm_ran` and `c_llm_flags` (answers that flag
  something). The 18 detail columns and `REXP` (0.99 correlated with `EXP`) are not inputs.
- **A blank `head_from_fork` means a fork:** the fork was deleted after the PR; when the webhook fires it exists.
- **Rows (`training_rows`):** score on PRs with a known outcome, opened at least 90 days before the data was
  observed, whose at-open diff is not `final`, by outside contributors (not a bot, not a confirmed maintainer).
  Train on those with an exact diff, each weighted by (scored rows of its repo and class) / (exact ones among them),
  times 1 / sqrt(PRs in its repo).
- **Tests:** `pipelines/training/tests/test_outcome_features.py` runs a cut test (no feature changes when everything
  after a date is unknown), a stale-open test (scrambling those rows' outcomes changes nothing), and checks that the
  lists still match `contracts/mined_dataset.schema.json`. CI's `pipelines` job runs them.

## Options not taken

| Option | Why not |
| --- | --- |
| Keep the leaky columns | Each carries the outcome; `author_association` alone is worth about +0.12 skill |
| Use the miner's history columns only | Their point-in-time correctness cannot be checked from the table; ours is tested |
| Keep the 18 LLM detail columns | Compressing them into two columns cost nothing (0.245 against 0.244 by PR) |
| Train on recovered diffs too | They carry a fingerprint; exact rows reweighted scored 0.290 / 0.234 against 0.280 / 0.226 |
| Drop recovered diffs without reweighting | Training then under-represents rework and the calibration breaks |
| One model per repo | 0.273 against 0.292 for one shared model that knows the repo (by PR) |
| More history (last outcomes, recent windows, size against recent merges) | No gain: 0.285 / 0.232 against 0.291 / 0.235 |
| Author history across repos | No gain (0.278 against 0.277 by PR), and it needs an organisation-wide view |

## Consequences

- **Measured value:** author history adds about +0.025 by PR and +0.027 by repo; repo history adds +0.007 by PR
  and +0.026 by repo, so it matters most for repos a model has not seen.
- **Team comparison:** in the team's paired comparison on 64,788 PRs, the model built on these features scored
  skill 0.205 against 0.192 for the next best, and was best on every outcome AUROC.
- **Serving:** history features need every earlier PR of the repo, so the scorer reads a repo's past PRs when Riffle
  is installed and keeps them current.
- **Invariants:** every score is point-in-time, and the cut test checks it. The LLM is not on the scoring path: its
  flags are inputs that may be null (`c_llm_ran = 0`), and the model must score without them. Without the LLM
  columns, skill falls by 0.009 by PR and 0.011 by repo. Nothing leaves the installation: history uses only the
  installation's own PRs.
- **Miner and contracts:** no change. A renamed or removed miner column fails the contract test.

## Revisit if

- The miner records `author_association` or the PR body as at open: those columns stop being leaks.
- The full corpus (43 repos) changes which feature groups help.
- A time-based split (train on older PRs, test on newer) ranks the feature groups differently.

## Open

- `max_file_post_merge_ci_fail_rate` and `author_post_merge_ci_fail_rate` depend on CI history the miner could only
  see for about 90 days; ADR 0021 treats this as a date leak for the breakage target. Their effect on the outcome
  model is not measured.
- Author identity is the lower-cased commit email, so an author with several emails looks like several people.
- How the scorer stores and updates repo history in production is not designed.
