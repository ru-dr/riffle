# contracts

Shared JSON Schemas (draft 2020-12) — **the source of truth** for everything
that crosses a service boundary, and for the config files organisations
write. Three languages read these; nothing else defines the wire format or
the config format. Any change to a schema here is a breaking change.

Every rule kind, value, setting and default is listed in
[`REFERENCE.md`](REFERENCE.md), generated from the schemas.

## Wire formats

| Schema | Producer | Consumer | Owner |
| --- | --- | --- | --- |
| `pr_event.schema.json` | `intake` | `scorer` | #8 GitHub App |
| `score_result.schema.json` | `scorer` | `app` | #9 Inference |
| `explain.schema.json` | `scorer` → `explainer` (`POST /v1/explain`) | `scorer` | #9 Inference |
| `mined_features.schema.json` | `scorer` (extractor), `pipelines/ingestion` (repo miner) | `scorer` (model), narrator, `pipelines/training` | #1 Features |
| `outcome_import.schema.json` | Organisation systems (`POST /v1/outcomes`) | `pipelines` (label mining) | #5 Ingestion |
| `mined_history.schema.json` | `miner` (Go, ADR 0004) | `pipelines`, `scorer` | #5 Ingestion |
| `mined_dataset.schema.json` | `pipelines/ingestion` (repo miner), one training row per closed PR | `pipelines/training` | #5 Ingestion |
| `mined_snapshot.schema.json` | `pipelines/ingestion` (repo miner `snapshots.py`), one row per PR snapshot | `pipelines/training` | #5 Ingestion |
| `mined_commit_features.schema.json` | `pipelines/ingestion` (repo miner), code features per head commit | `pipelines/training` | #5 Ingestion |

Shapes are specified in the root `README.md` under **Contracts**; write the
schema files to match. `pr_event`, `score_result` and `explain` have no schema
file yet; their rows describe the target.

Planned: `feature_vector.schema.json`, the full fixed model input (repo
history, blame, diff, checks, mined, declared columns).

## Configuration

| Schema | Written by | File | Read by | Owner |
| --- | --- | --- | --- | --- |
| `org_config.schema.json` | Organisation | `.github/riffle.yml` in the org `.github` repo, then in each repo | `app`, `scorer`, dashboard | #8 GitHub App |
| `org_rules.schema.json` | Organisation | `riffle/rules.yml` in the org `.github` repo, `.github/riffle/rules.yml` in each repo | `scorer` (rule compiler), `pipelines/training` (label miner) | #1 Features |

Loading order: built-in defaults → org file → repo file (repo wins) →
`organization.locked_keys` restored from the org file. Files are read from
the default branch only. Examples live in `fixtures/config/`.

## Custom keywords

JSON Schema ignores these; our loaders and compiler read them.

| Keyword | Meaning |
| --- | --- |
| `x-riffle-version` | The schema's current version. Config files must match its major; history in `docs/contracts.md` |
| `x-riffle-merge` | `union` or `replace` for arrays when org and repo files merge. Default `replace` |
| `x-riffle-org-only` | Ignored in repo files |
| `x-riffle-sink` | Where a rule goes: `feature`, `label`, `link`, `filter` |
| `x-riffle-compiles-to` | Model columns a rule kind produces |
| `x-riffle-model-columns` | The fixed declared column set. Changing it is a major version bump and a retrain |
| `x-riffle-phase` | `later` means specified but not built in v1 |
| `x-riffle-decision-pending` | Needs a decision record before the default is final |
| `x-riffle-code-checks` | Cross-field rules the schema cannot express; code must enforce them |

## Rules

- Adding a required field is breaking. Add it optional, backfill, then require.
- `explanation` is nullable **by design**. Null means the explainer degraded;
  the rank is still valid. Never make it required.
- `delivery_id` is the idempotency key end to end. It must survive every hop.
- `model_version` is pinned per request and covers the global model, the
  per-repo layer, the feature extractor, and the compiled rules hash. A
  request never mixes versions.
- Bump every consumer in the same PR as the schema.
- Settings never change what the model sees or learns. Anything that does
  belongs in `org_rules`, where it is hashed into `model_version`.
- `null` means not declared or not enough history. `0` means measured and
  found none. Never coerce one into the other.
- Mined features are point-in-time: only data from before the PR opened, and
  only labels already mature at that moment.
- Config files use `schema_version: "major.minor"`. A minor bump adds
  optional keys; a major bump renames or removes, and old keys stay accepted
  for one minor version.
- An invalid config file keeps the last valid version and posts one notice.
  Scoring never stops on bad config.
- Credentials appear only as secret references, never as values.
- Nothing leaves by default: outcome sharing is off, and no schema here
  carries code, diffs, or author identities out of the installation.

## Flat form

Every contract's data flattens to key-value pairs without loss (ADR 0012):
each key is the value's JSON Pointer, the same notation as
`organization.locked_keys`.

```python
from flatten import flatten, unflatten
flatten({"labels": {"maturity_days": 30}, "rules": [{"id": "a", "kind": "pr_label"}]})
# {"/labels/maturity_days": 30, "/rules/0/id": "a", "/rules/0/kind": "pr_label"}
unflatten(flat, schema)   # back to the document; the schema decides list vs object
```

- Lists of scalars stay one value (`/scope/quiet_authors`); lists of objects
  are split by index. `~` is `~0` and `/` is `~1` inside a key.
- `flat_keys.json` lists every key pattern of every schema (`*` is a list
  index, `{key}` a free-form key) with its types and default. It is
  generated with `REFERENCE.md`.
- The schemas stay nested; the flat form is derived, never edited.

## Validation

- `python contracts/gen_reference.py` regenerates `REFERENCE.md` and
  `flat_keys.json`. CI runs it with `--check` and fails if a schema changed
  without regenerating.
- `python -m pytest contracts/tests` checks that every schema's data
  round-trips through the flat form, every `oneOf` branch included.
- `pipelines/ingestion/repo_miner/tests/test_dataset_contract.py` checks that
  the repo miner writes exactly the columns of `mined_dataset.schema.json`, that
  its rows validate, and that they flatten losslessly. `test_snapshots.py` does
  the same for `mined_snapshot` and `mined_commit_features`, and
  `test_contract_sync.py` checks that each mined schema's recorded miner version
  is not ahead of `version.py` and has a row in `docs/contracts.md`.
- `contracts/tests/test_flatten.py` round-trips the files in `fixtures/config/`
  through the flat form. Nothing in CI yet validates the schemas against the
  draft 2020-12 meta-schema or the fixtures against their schemas, and there is
  no `invalid/` fixture directory.
- `x-riffle-code-checks` rules are tested in the consuming service, not here.

## Open decisions

- Band floors: no ADR yet. Marked `x-riffle-decision-pending` in
  `org_config.schema.json`.
