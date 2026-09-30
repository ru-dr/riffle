Every shape that crosses a service boundary is defined as a JSON Schema
(draft 2020-12). Three languages read them, and nothing else defines the wire
format. **Any change to a schema is a breaking change.**

## Wire formats

| Schema | Producer | Consumer |
| --- | --- | --- |
| `pr_event` | `intake` | `scorer` |
| `score_result` | `scorer` | `app` |
| `explain` | `scorer` → `explainer` (`POST /v1/explain`) | `scorer` |
| `mined_features` | `scorer` (extractor) | the model, the narrator, training |
| `outcome_import` | your systems (`POST /v1/outcomes`) | label mining |
| `mined_history` | `miner` | pipelines, `scorer` |

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

Written by `scorer`, read by `app`.

```json
{
  "delivery_id":   "string",
  "tenant_id":     "string",
  "pr_number":     0,
  "risk_score":    0.0,
  "rank_band":     "review_first | standard | senior_recommended",
  "model_version": "string, pinned for this request",
  "features":      { "...": "the vector used, for audit" },
  "explanation":   "string or null when the explainer timed out",
  "scored_at":     "RFC3339 timestamp"
}
```

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
