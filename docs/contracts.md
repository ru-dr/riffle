# Contracts version history

Current versions are in each schema's `x-riffle-version`. Config files set
`schema_version: "major.minor"`; any minor within the current major is accepted.

Minor: adds optional keys or changes a default. Major: renames or removes keys;
old keys stay accepted, marked deprecated, for one minor version.

## `org_rules.schema.json`

| Version | Date | Changes | Migration |
| --- | --- | --- | --- |
| 5.3 | 2026-09-29 | `labels.target_sources`, default `revert`, `ci_fail`, `hotfix` (ADR 0002). Label weights apply to the per-repo layer only | None. Files without the key get the ADR 0002 target |
| 5.2 | 2026-09-29 | `labels.unobserved` gains `downweight`, now the default. New `labels.unobserved_weight`, default 0.5 | Set `unobserved: mask` to keep 5.1 behaviour |
| 5.1 | 2026-09-29 | `labels.unobserved` (mask), `mining.flaky_detection`, `model.backfill`, `policy.languages` for `no_import_from` | None |
| 5.0 | 2026-09-29 | Split from the combined org config. Top-level `scope` renamed `level`. New `model` block with `per_repo_layer` and `disabled_features`, moved from settings. Version format changed to `"major.minor"` | Rename `scope` to `level`. Move `per_repo_layer`, `disabled_features`, and label weights out of settings |

Earlier drafts (1–4) were never merged and need no migration.

## `org_config.schema.json`

| Version | Date | Changes | Migration |
| --- | --- | --- | --- |
| 5.0 | 2026-09-29 | First merged version. Combines the team's v1 settings draft with the rules split: loading order, `locked_keys`, `mode`, commands, reviewer routing, availability, review targets, reminders, size labels, LLM privacy (default `provider: none`), audit, `floors` (decision pending), `quiet_authors`, holdout, tie-break, per-component overrides | None; first release |

## `mined_features.schema.json`

| Version | Date | Changes |
| --- | --- | --- |
| 1.0 | 2026-09-29 | Nine repo-local columns and narrator evidence |

## `outcome_import.schema.json`

| Version | Date | Changes |
| --- | --- | --- |
| 1.0 | 2026-09-29 | `POST /v1/outcomes` body |
