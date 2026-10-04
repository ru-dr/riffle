# 0008: Extractor output columns added and removed

- **Date:** 2026-10-03
- **Status:** Proposed
- **Owners:** #1 Feature engineering

## Context

The extractor's output columns have changed since the feature map in `riffle-data-research.pdf` was written. Five columns are new and one is removed. `label_szz_bug` and `label_ci_fail_optional` already exist and are unchanged. Storing `label_mature` fixes one maturity threshold into the data, while `contracts/org_rules.schema.json` already sets maturity through `labels.maturity_days`.

## Decision

- Add these columns:

| Column | Type | Meaning |
| --- | --- | --- |
| `days_to_revert` | number or null | Days from merge to the first revert of the PR. Null if none observed within 90 days |
| `days_to_hotfix` | number or null | Days from merge to the first hotfix-labelled PR touching the same lines, matched as for `label_hotfix`. Null if none observed within 90 days |
| `days_to_szz_fix` | number or null | Days from merge to the earliest later fix of a line the PR introduced. Null if none observed within `labels.szz_maturity_days` |
| `merge_via` | enum | How the PR landed, using the `merge_signal` values in `contracts/org_rules.schema.json`: `github_merge`, `linked_commit_on_default`, `bot_close_with_commit`, `direct_push` |
| `mined_at` | timestamp, UTC | When the row was extracted |

- Remove `label_mature`. Training computes maturity as `mined_at - merged_at >= labels.maturity_days`, and uses `labels.szz_maturity_days` for SZZ.
- None of the new columns are model inputs. They are outcomes or metadata, known only after merge.
- A null means "not observed by `mined_at`", not "never happened".

## Options not taken

| Option | Why not |
| --- | --- |
| Keep `label_mature` | Fixes one maturity threshold into the data; changing it needs a full re-mine |
| Match hotfixes by file | Disagrees with `label_hotfix`, so lag and label would describe different events |
| `merge_via` values `github` and `landed_commit` | Do not match the `merge_signal` values in the rules schema |

## Consequences

- Output grows from 153 to 157 columns (+5, −1).
- `pipelines/training/` applies maturity from the rules before training.
- The feature dictionary drops `label_mature` and lists the five new columns.
- The columns go into `contracts/feature_vector.schema.json` as non-input outcome columns once that schema exists.
- `days_to_revert` and `days_to_hotfix` feed the mined feature `path_detection_lag_days`. To keep scores point-in-time, only past PRs merged before the scored PR opened, with labels mature at that moment, may contribute. No other conflict with the invariants.

## Revisit if

- `label_source.window_days` in `contracts/org_rules.schema.json` allows windows above 90 days.
- A label source other than revert, hotfix, or SZZ needs its own lag column.

## Open

- Whether the miner can detect `bot_close_with_commit` and `direct_push`, or only `github_merge` and `linked_commit_on_default` for now.