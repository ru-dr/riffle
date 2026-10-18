# 0011: Domain models as the base layer

- **Date:** 2026-10-18
- **Status:** Proposed
- **Owners:** Modelling workstreams #2 and #3

## Context

ADR 0001 makes one global model the base for every repository, with a per-repo
layer on top. ADR 0009 adds a second target, so the base is now two models:
`label_merged` on every closed PR and `label_bad` on merged PRs. The final
submission needs a claim we can check and defend, and a claim about one domain
is easier to test on repositories like the ones a user runs. The corpus is
already grouped into categories (ADR 0007, `site/content/docs/dataset-plan.md`),
and review culture, merge policy and bot traffic differ between them. Adding a
domain later must not need changes to the scorer, the extractor's core columns,
or the other domains.

## Decision

- **Base layer is per domain.** Each domain has its own gradient-boosted
  models, one per target in ADR 0009 (`label_merged`, `label_bad`). The
  per-repo layer from ADR 0001 sits on top unchanged: same fitting, same
  `layer_weight` ramp, same automatic drop to 0.0 when it ranks worse than the
  base on its holdout.
- **The final submission ships one domain.** Which one is under Open.
- **Domain registry.** One file per domain at `contracts/domains/<id>.yaml`,
  validated by a new `contracts/domain.schema.json`. Fields:
  - `id`, `version`
  - `membership`: an explicit repository list, plus optional matching rules
    (primary language, topics) for repositories that install later
  - `features`: the core feature set version and the list of domain extras
  - `targets`: defaults to `label_merged` (ADR 0009) and `label_bad`
    (ADR 0002)
  - `eval`: held-out repositories and the time-split date
  - `models`: one MLflow reference per target, written when promoted
- **Routing.** The scorer maps a repository to a domain once, at install, and
  caches it. Routing is per repository, never per PR. A repository that
  matches no domain, or more than one, gets no domain score: the PR still
  reaches the queue, unranked and flagged, as the scorer's fail-open rule
  already requires.
- **Features.** Every domain uses the same core columns, computed from the PR
  as it opened (ADR 0010). Domain-only features use the prefix `dx_<domain>_`
  and never change core columns or another domain's columns. A core change is
  a MAJOR version under the extractor's versioning rules. Mining and serving
  use the same extractor code.
- **Training.** One training entry point takes the domain id
  (`train --domain <id>`). It reads the registry, selects that domain's rows,
  trains each target, calibrates per domain, and registers the models and
  holdout reports in MLflow. Every domain uses the same code path.
- **Promotion.** A domain model serves only if it beats FIFO and its own
  previous version on that domain's held-out repositories, per target. Those
  repositories are never in that domain's training data.
- **Pinning.** `model_version` from ADR 0001 also pins `domain_id` and the
  registry file's `version`.
- **Mining.** Each line in the miner's `repos.txt` gets a domain tag, and
  every mined row records it. The submission domain is mined first.
- **Adding a domain** means: write the registry file, list and mine its
  repositories, run `train --domain <id>`, pass promotion. No scorer code
  changes.

## Options not taken

| Option | Why not |
| --- | --- |
| Global models as the base (ADR 0001) | A claim averaged over every category is harder to check on one user's kind of repository |
| One pooled model with domain as a feature | Ties every domain to one release, so adding or fixing a domain retrains all of them |
| Per-domain models plus a general fallback model for the submission | A second set of models to train, calibrate and promote before the submission. Kept as a later extension (see Revisit if) |
| Choosing the domain per PR | A repository's PRs could land in different models, so scores would not be comparable inside one queue |

## Consequences

- When this ADR is accepted, ADR 0001's Status changes to "Superseded by
  0011". Its per-repo layer decisions carry over unchanged through this ADR.
- ADR 0001's kill criterion now reads: if domain model plus layer does not
  beat both the domain model alone and FIFO in the backtest on that domain's
  held-out repositories, Riffle repositions as an explanation layer.
- ADR 0009's open question on training `label_merged` on agent and human PRs
  together is answered per domain, not once for all.
- New contract: `contracts/domain.schema.json` and `contracts/domains/`.
- Scorer: adds the repository-to-domain lookup and the "no domain" fail-open
  path. Training: takes `--domain`. Data ingestion (#5): domain tag in
  `repos.txt` and on every mined row.
- Repositories outside the shipped domain get no ranking until their domain
  exists. The scorer flags this; it does not guess.
- Metrics are reported per domain, per target and per repository. Results
  from different domains are not pooled into one headline number.
- Invariants: no conflict. The LLM stays off the scoring path, nothing skips
  review, Riffle never blocks merges, nothing leaves the installation, and
  scores stay point-in-time (ADR 0010).

## Revisit if

- The domain models do not beat models trained on the whole corpus when
  tested on that domain's held-out repositories.
- A domain has too few positive examples to train or evaluate on its own,
  measured against the "reliable positive examples" target in the dataset plan.
- Users install on enough repositories outside every domain that a general
  fallback model is needed.

## Open

- Which domain the submission ships. Candidates are the corpus categories in
  the dataset plan.
- Held-out repositories per domain. The current split puts 15 validation and
  15 test repositories across all categories, so one domain may have only a
  few of each.
- The matching rules for repositories that install later, and what to do
  with monorepos that span domains.
- Whether the domain registry should live under `contracts/` or with the
  training pipeline.
