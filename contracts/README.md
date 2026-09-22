# contracts

Shared JSON Schemas — **the source of truth** for everything that crosses a
service boundary. Three languages read these; nothing else defines the wire
format. Any change to a schema here is a breaking change.

| Schema | Producer | Consumer |
| --- | --- | --- |
| `pr_event.schema.json` | `intake` | `scorer` |
| `score_result.schema.json` | `scorer` | `app` |

Shapes are specified in the root `README.md` under **Contracts**; write the
schema files to match.

Rules:

- Adding a required field is breaking. Add it optional, backfill, then require.
- `explanation` is nullable **by design**. Null means the explainer degraded;
  the rank is still valid. Never make it required.
- `delivery_id` is the idempotency key end to end. It must survive every hop.
- `model_version` is pinned per request. A request never mixes a new model
  with an old feature extractor.
- Bump every consumer in the same PR as the schema.
