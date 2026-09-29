# 0006: Config file versioning and compatibility

- **Date:** 2026-09-29
- **Status:** Proposed
- **Owners:** #5 Data ingestion (config compiler, ADR 0004), with #8 GitHub App and #1 Feature engineering as reviewers

## Context

Config files declare `schema_version: "major.minor"`, and each schema records
its current version in `x-riffle-version`. Until now the schemas pinned the
major in their pattern (`^5\.[0-9]+$`), so a file one major behind failed
validation before any migration could run, and a file written for a newer
minor passed silently. Version strings also compare wrongly as text:
`"5.10"` sorts before `"5.9"`. The compiler needs one rule that decides
compatibility and records what it decided.

## Decision

- **Schema:** `schema_version` in `org_config` and `org_rules` accepts any
  well-formed version, `^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$`. It rejects
  `05.1`, `5`, and `v5.0`. Compatibility is not the schema's job.
- **Compiler:** after YAML parsing and before schema validation, the Go
  compiler parses both the file's version and the schema's `x-riffle-version`
  as two integers and compares them:

  | File vs schema | Result |
  | --- | --- |
  | Same major, minor ≤ schema | Accept |
  | Same major, minor > schema | Fail: written for a newer Riffle, upgrade |
  | One major behind | Accept, map deprecated keys, warning diagnostic |
  | Two or more majors behind, or any major ahead | Fail |

  A one-major-behind file fails until a mapping for that major exists.
- **Failure:** follows fail-open. The last valid compiled config stays in
  force and one neutral notice is posted.
- **Output:** the compiled flat output records `/meta/schema_version` (what
  the file declared) and `/meta/compiled_against` (the schema the compiler
  used). The unflattener reads `compiled_against` to pick the schema.
- **Deprecation:** a major bump may rename or remove keys. Old keys are
  mapped for exactly one major, then rejected.
- **Golden files:** keep a fixture that declares `5.0` and compiles against
  `5.3`, since an older minor against a newer schema is the common case.
  Numbers keep their written form in output (`1.0` stays `1.0`), which in Go
  means decoding with `json.Number`.

## Tests

- `5.0` and `5.3` pass.
- `5.4` fails with the upgrade message.
- `4.9` fails today; passes with a warning once a 4-to-5 mapping exists.
- `3.0` and `6.0` fail.
- `5.10` compares higher than `5.9`.

## Options not taken

| Option | Why not |
| --- | --- |
| Pin the major in the schema pattern | Blocks migration of older files before the compiler can map them |
| Compare versions as strings | `5.10` sorts before `5.9` |
| Accept newer minors silently | Unknown keys or defaults would be applied wrongly with no warning |
| Keep old keys for one minor, not one major | Minors only add optional keys, so there is nothing to deprecate within a major |
| Leave the version out of the compiled output | Readers could not tell which schema produced a compiled config |

## Consequences

- The contracts README and `docs/contracts.md` describe this rule; the old
  "accepted for one minor version" wording is removed from both.
- `/meta/*` keys become part of the compiled output that `scorer` and `app`
  read.
- `REFERENCE.md` is regenerated whenever a pattern or description changes.

## Open

- Whether `/meta/*` keys are part of the rules hash input.

## Revisit if

- Two majors in a row need mappings, which would suggest majors are bumped
  too often.
