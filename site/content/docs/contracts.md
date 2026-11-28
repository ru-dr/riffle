Every shape that crosses a service boundary is defined as a JSON Schema
(draft 2020-12). Three languages read them, and nothing else defines the wire
format. **Any change to a schema is a breaking change.**

## Wire formats

| Schema | Producer | Consumer |
| --- | --- | --- |
| `pr_event` | `intake` | `pr-miner` |
| `installation_event` | `intake` | `app` (install and uninstall workflow) |
| `pr_features` | `pr-miner` | `scorer` |
| `score_result` | `scorer` | `app` |
| `score_log` | `scorer` | the score-log writer, training |
| `explain` | `scorer` → `explainer` (`POST /v1/explain`) | `scorer` |
| `mined_features` | `scorer` (extractor) | the model, the narrator, training |
| `outcome_import` | your systems (`POST /v1/outcomes`) | label mining |
| `mined_history` | `miner` | pipelines, `scorer` |
| `mined_dataset` | the repo miner (one row per closed PR) | training |
| `mined_snapshot` | the repo miner (one row per PR moment while open) | training |
| `mined_commit_features` | the repo miner (one row per head commit) | training |

## PrEvent

Published by `intake` for every verified delivery.

```json
{
  "delivery_id": "string, the X-GitHub-Delivery header",
  "tenant_id":   "string, installation id",
  "repo":        "string, owner/name",
  "pr_number":   0,
  "action":      "opened | synchronize | reopened",
  "head_sha":    "string",
  "received_at": "RFC3339 timestamp"
}
```

## ScoreResult

Written by `scorer` after the score is stored, read by `app`. The full list of keys is in
`contracts/score_result.schema.json`. A PR is ranked (`rank_score`, `rank_band` and
`model_version` set) or unranked with a reason, never half.

```json
{
  "delivery_id":          "string",
  "tenant_id":            "string",
  "repo":                 "owner/name",
  "pr_number":            0,
  "head_sha":             "40 hex characters",
  "trigger":              "opened | synchronize | ...",
  "p_lands_clean":        0.0,
  "p_lands_after_rework": 0.0,
  "p_goes_nowhere":       0.0,
  "p_merge":              0.0,
  "rank_score":           0.0,
  "rank_mode":            "merge_first | risk_first | custom",
  "rank_band":            "senior_recommended | review_first | standard | null",
  "model_version":        "riffle-outcome-setup-g/7/new_repos, pinned for this request",
  "miner_version":        "7.2.2",
  "layer_weight":         0.0,
  "unranked_reason":      "model_unavailable | not_outside_contributor | scoring_failed | invalid_input | null",
  "explanation":          "string or null",
  "scored_at":            "RFC3339 timestamp"
}
```

The model's inputs are not in the result. They go to the score log (`score_log`), one row per
score.

## Rules for changing a schema

- Adding a required field is breaking. Add it optional, backfill, then require it.
- `explanation` is nullable **by design**. Never make it required.
- `delivery_id` is the idempotency key end to end. It must survive every hop.
- Bump every consumer in the same change as the schema.
- Nothing leaves by default: no schema carries code, diffs or author
  identities out of the installation.

## Validation

| Check | Runs |
| --- | --- |
| Every schema against the draft 2020-12 meta-schema | CI, every change |
| Every fixture config file against its schema; files under `invalid/` must fail | CI, every change |
| The generated reference matches the schemas | CI, `gen_reference.py --check` |
