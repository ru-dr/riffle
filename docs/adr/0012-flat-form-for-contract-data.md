# 0012: Flat key-value form for contract data

- **Date:** 2026-10-19
- **Status:** Proposed
- **Owners:** #1 Feature engineering, with #5 Data ingestion

## Context

The schemas in `contracts/` describe nested data: config files with lists of
rules, the mined-features document with its evidence lists, the history
manifest with its tables. Training code, the feature store and config tooling
want plain key-value pairs, and each consumer flattening data its own way would
give the same value different names. `organization.locked_keys` in
`contracts/org_config.schema.json` already names config keys as JSON Pointers
(`/llm/provider`). The repo miner's training rows had no contract of their own
until `contracts/mined_dataset.schema.json`, and nothing checked that the miner
and that contract agree.

## Decision

- Every contract's data has one flat form: a map from JSON Pointer (RFC 6901)
  to value, implemented in `contracts/flatten.py` (standard library only).
  - A value is a scalar, a list of scalars kept whole, or an empty object or
    list.
  - A list that holds objects or lists is split by index: `/rules/0/kind`.
  - `~` is written `~0` and `/` is written `~1`, so any object key round-trips,
    including free-form keys such as `house_terms` entries.
- `unflatten(flat, schema)` rebuilds the document. The schema decides whether
  `/x/0` is a list item or an object key `"0"`, so data is unflattened against
  the schema it was written against.
- The schemas stay nested and remain the source of truth. The flat form is
  derived and never edited by hand.
- `contracts/flat_keys.json` lists every key pattern of every schema's flat
  form (`*` for a list index, `{key}` for a free-form key) with its JSON types
  and default. A default is listed only when every branch agrees (for
  `/rules/*/...`, every rule kind); otherwise the entry says
  `default_varies`. `contracts/gen_reference.py` writes it beside
  `REFERENCE.md`, and `--check` covers both.
- `contracts/tests/test_flatten.py` builds, for every schema, documents that
  touch every property, every `oneOf` branch (all 22 rule kinds) and every
  free-form map, and checks that each round-trips and that each flat key is in
  `flat_keys.json`. The config fixtures are round-tripped too.
- The repo miner's output must match `contracts/mined_dataset.schema.json` in
  both directions. `pipelines/ingestion/repo_miner/tests/test_dataset_contract.py`
  mines a small repository with stages on and off and fails on any column
  missing from either side, any row that doesn't validate, or any row that
  doesn't round-trip.
- CI runs `gen_reference.py --check`, the contract tests and the miner tests on
  every pull request.

## Options not taken

| Option | Why not |
| --- | --- |
| Dotted keys (`labels.maturity_days`) | Breaks on keys that contain dots or slashes, and lists need a second convention |
| Flatten the schemas themselves | A flat file is no longer a JSON Schema; validators and `gen_reference.py` would stop working |
| Make every contract flat by design | Rule lists and evidence lists are lists of objects; flattening them in the schema is a breaking change to every config file and wire format |
| Encode lists as JSON strings in one value | Not key-value at the leaves, and every consumer has to parse it again |
| Leave flattening to each consumer | The same value would get different names in training, the feature store and tooling |

## Consequences

- New: `contracts/flatten.py`, `contracts/flat_keys.json`,
  `contracts/tests/test_flatten.py`, and a `contracts` job in
  `.github/workflows/ci.yml`.
- Repo miner v2.3.0: with `--no-merge-outcome` the 13 merge-outcome columns
  (`label_merged`, `close_reason`, `created_at`, `closed_at`, ...) are now
  written as `null` instead of left out, as the miner README already said. No
  other output changes.
- A miner change that adds or removes a column fails CI until the contract
  changes in the same pull request.
- No schema changes. The flat form adds no new information, so nothing new
  leaves the installation.
- Invariants: no conflict.

## Revisit if

- A schema gains a list whose items are a mix of objects and scalars, which
  the flat form can only split by index.
- A consumer needs a flat key for a value inside a list of scalars.
