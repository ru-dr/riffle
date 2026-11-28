# 0027: Score live PRs from rows made by the repo miner

- **Date:** 2026-11-27
- **Status:** Proposed
- **Owners:** #9 Inference, with #5 Data ingestion (to confirm, see Open)

## Context

The outcome model (setup G, ADR 0023) was trained on the repo miner's dataset table. Its production path, `score(table, model_dir)` in `pipelines/training/outcome/experiments/notebook_build/pipeline_src.py`, takes that table: the PR to score plus the repo's earlier PRs, from which it builds the 30 history features, and reads 131 miner columns (`MINER_FEATURES` in `pipelines/training/outcome_features.py`). intake publishes only identifiers (`contracts/pr_event.schema.json`), and nothing in the live system makes a row in the miner's shape. A row computed by different code would differ from the training rows in ways no test would catch. In the mined data, the LLM answers are empty in 70% of rows, Semgrep in 99.6% and OSV in 84%, while the file-history and complexity columns are almost never empty.

## Decision

- **A live PR reaches the scorer as a row of `contracts/mined_dataset.schema.json`**, made by the miner's own code, inside a new message `contracts/pr_features.schema.json` (version 1.0) on a new topic, `pr-features`.
- **A new service, `pr-miner`** (namespace `riffle-workers`, Google account `riffle-staging-pr-miner`), reads `pr-events` (subscription `pr-miner-pr-events`), makes the row for that one PR with the miner's `mine_pr()`, and publishes it. The scorer reads `pr-features` (subscription `scorer-pr-features`) instead of `pr-events`.
- **The LLM, Semgrep and OSV are off** for live rows; their columns are null and the message lists them in `disabled_sources`. The file-history and complexity columns are computed, from a copy of the repo the pr-miner keeps.
- **While the PR is open its outcome and label fields are null.** The scorer adds the repo's earlier rows from a history store and calls the training code's `score()`.
- **The row's `miner_version` is pinned**: it must be the version the model in service was trained with, and it is part of `model_version`.
- **At install, the history store is filled by running the miner unchanged** on the repo, as a bounded Kubernetes Job (the backfill of ADR 0001 and ADR 0025).
- **Checks:** `contracts/tests/test_pr_features.py` (the message and its row against both contracts) and `pipelines/training/tests/test_pr_features_model_input.py` (the row becomes the 161 model inputs in order), on `fixtures/pr_features/pull_request.opened.json`, made from a real mined row.

## Options not taken

| Option | Why not |
| --- | --- |
| Compute the features in the scorer with new code | Two implementations of the same features drift apart; training and serving would differ |
| Retrain the model on features the GitHub API gives directly | The team chose to keep the current model; it would also drop the file-history features |
| Leave the file-history and complexity columns empty | They were almost never empty in training, so the model's answers would be unreliable |
| Mine inside the scorer | Mining one PR takes seconds to minutes and needs repo copies on disk; scoring takes about a millisecond and must stay light |
| Call the LLM for the seven LLM columns | The LLM is never on the scoring path, and the diff would leave the installation |

## Consequences

- New: `contracts/pr_features.schema.json`, `fixtures/pr_features/`, the two tests, the `pr-features` topic in `infra/pubsub/topics.json`, the `pr-miner` account in `infra/terraform/envs/staging/09-identity.tf` (reads `pr-events`, publishes `pr-features`, the GitHub App key, the `snapshots` bucket, the tenant key), its Kubernetes service account and secret in `infra/k8s/platform`, and `services/pr-miner/README.md`.
- Changed: the scorer reads `pr-features` and may read the `snapshots` bucket and unwrap tenant keys, for the history store.
- The miner needs three changes before the pr-miner can run: log in as the GitHub App (an installation token) instead of personal tokens; reuse a saved clone and history index instead of rebuilding them per run; and be callable as a library. They are made in `pipelines/ingestion/repo_miner` under its own rules (version bump, `ARCHITECTURE.md`).
- The root `README.md` and `site/content/docs` describe the scorer as extracting features itself; they are updated when this is accepted.
- Invariants:
  - **Point-in-time scoring:** history comes only from PRs that closed before this PR opened (`score()` already does this).
  - **The LLM is never on the scoring path:** the LLM columns are null.
  - **Nothing leaves the installation:** no diff is sent outside.
  - **One pinned `model_version`:** it now includes the miner version.

## Revisit if

- Mining one live PR routinely takes longer than the time the team agrees a PR may wait for its rank.
- The model is retrained on a different feature set.
- A shared feature package (`libs/features`, ADR 0005) replaces the miner's feature code for both training and serving.

## Open

- Owners: #9 Inference and #5 Data ingestion are a proposal; confirm them.
- How long a PR may wait for its rank, given mining time.
- Where the history store lives (Parquet per tenant in the `snapshots` bucket, or a database table) and how outcomes are added to it.
- The model scores only outside contributors (not bots, not confirmed maintainers); what is shown for other PRs.
- How the repo copies are stored in the cluster (disk per pod, or a shared file store) and kept up to date.
