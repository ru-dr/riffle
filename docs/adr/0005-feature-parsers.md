# 0005: Feature parsers as a shared Python package

- **Date:** 2026-09-29
- **Status:** Proposed
- **Owners:** #1 Feature engineering, with #9 Inference as reviewer

## Context

Most of the model's inputs come from parsing a PR: its diff, its paths, its
dependency manifests, the changed code, and any API specs or migrations.
Both `scorer` (serving) and `pipelines/` (training and backtest) need exactly
the same parsers, or features drift between training and serving. Until now
the parsers were mentioned only in the README and ADR 0004, with no agreed
home, interface, versioning, or failure behaviour. Some parsers are cheap and
some are slow, and the route must post quickly.

## Decision

- **Home:** one Python package, `libs/features/`, imported by `scorer` and
  `pipelines/`. Neither owns it; neither copies it.
- **Parsers and stages:**

  | Parser | Produces | Stage |
  | --- | --- | --- |
  | `diff` | Hunks, file status, line counts, binary and generated detection, languages | 1 |
  | `paths` | Path classes, components, tiers, sensitivity tags, `touches_*` flags, from compiled rules | 1 |
  | `manifests` | Dependencies added, upgraded, downgraded, major bumps | 1 |
  | `secrets` | `hardcoded_secret_added`, and redaction before any LLM call | 1 |
  | `code` (tree-sitter) | Functions modified, imports, bound and comparison edits, new config keys, observability delta, TODO and FIXME added | 2 |
  | `complexity` (lizard) | Complexity per function, `ccn_delta` | 2 |
  | `specs` | OpenAPI, proto, GraphQL breaking vs additive; SQL migration reversibility | 2 |

  Semgrep and the OSV/GHSA lookup are scanners, not parsers, and run in
  stage 2 beside them.
- **Interface:** every parser declares `name`, `version`, `stage`, the
  inputs it reads, the feature names it writes, and a timeout. It is a pure
  function of those inputs at the PR's base and head commits: no network,
  no clock, no global state. The scanners are the only stage 2 components
  allowed network access, and only when the installation permits it.
- **Registry:** each feature name belongs to exactly one parser. The registry
  is the source for the planned `contracts/feature_vector.schema.json`.
- **Versioning:** the feature extractor version is the set of parser
  versions, and it is pinned in `model_version`. Changing a parser's output
  bumps its version, and a version bump is a model change.
- **Failure:** a parser that errors or times out writes `null` for all its
  features and logs `riffle_parser_failures_total{parser}`. Scoring
  continues. Never `0`.
- **Budgets:** stage 1 parsers together stay under 500 ms at p95 so the route
  posts fast. Stage 2 has 60 seconds, then its features stay `null` and the
  PR is not rescored for that stage.
- **Languages:** tree-sitter grammars for Java, Python, TypeScript,
  JavaScript, Go, and Kotlin in v1, matching `policy.languages`. Other
  languages get `null` for code-parser features.
- **Tests:** every parser has fixture diffs with expected outputs in
  `libs/features/tests/`, plus one test that runs the same fixture through
  the training and serving entry points and requires identical output.

## Options not taken

| Option | Why not |
| --- | --- |
| Parsers inside `services/scorer/` | `pipelines/` would depend on a service, or copy the code |
| Parsers in Go (see ADR 0004) | Training is Python, so features would be computed in two languages |
| A parser service called over the network | A new contract, deploy, and version to pin, and a network hop inside scoring |
| All parsers in one stage | Slow parsers would delay the route past the latency budget |
| Failed parser writes 0 | Tells the model "measured, found none" when nothing was measured |

## Consequences

- New top-level `libs/` directory, with its own README.
- The README's Architecture row and Repository layout point to
  `libs/features/`.
- The ML team trains with random masking of stage 2 features, since they
  are often `null` at inference (stage 1 only, timeouts, unsupported
  languages).
- #10 adds `libs/features` tests to CI; they run for any PR touching
  `libs/`, `services/scorer/`, or `pipelines/`.
- `riffle_parser_failures_total` joins the metrics table.

## Revisit if

- Stage 1 regularly exceeds its 500 ms budget.
- A parser's null rate stays above 20% on supported languages.
- A language outside the v1 list becomes common in installed repos.

## Open

- Whether OSV/GHSA lookups use a local mirror for installations without
  network access, or stay `null` there.
- Exact Semgrep rule set and how it is pinned.
