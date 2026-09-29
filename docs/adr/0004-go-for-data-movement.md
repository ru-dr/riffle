# 0004: Go for data movement, Python for features

- **Date:** 2026-09-29
- **Status:** Proposed
- **Owners:** #8 GitHub App (Rudr), with #5 Data ingestion as reviewer

## Context

Three pieces of Riffle move and coordinate data rather than compute model
features: mining years of git and GitHub history on install, compiling org
config and rules into one effective version, and watching for outcomes after
merge. They are I/O-bound, long-running, and concurrent, and they are where
most of the distributed-systems risk sits: rate limits, retries, resumable
jobs, and duplicate events. Model features must be computed identically in
training and serving, and training is Python, so feature code cannot move.
The team also wants more Go experience beyond `intake`.

## Decision

- **Rule:** Go for components that move, coordinate, or compile data. Python
  for anything that computes a model feature or a label. No feature is
  computed in two languages.
- **1. History miner** (`services/miner/`, first). A Go CLI run as Kubernetes
  Jobs. Mines commits, file changes, PRs, reviews, check runs, and tags into
  Parquet, recent-first per `model.backfill`. Writes a manifest per run
  (`contracts/mined_history.schema.json`). Requirements:
  - one GitHub rate-limit budget shared across all workers of an installation,
    using conditional requests where possible;
  - checkpoints, so a crashed or preempted Job resumes rather than restarts;
  - idempotent writes keyed by `run_id`;
  - a bounded worker pool that respects training backpressure;
  - `coverage` fields that tell the label builder which sources are unobserved.
- **2. Config compiler** (`services/compiler/`, second). One Go binary merges
  org and repo settings, compiles rules, computes the rules hash, and stores
  the effective config in Postgres. `scorer` and `app` read that row and never
  merge config themselves. Keeps the last valid config on error.
- **3. Outcome watcher** (third, stretch). Watches for reverts, CI failures,
  and hotfixes after merge, and serves `POST /v1/outcomes`. At-least-once
  delivery, deduplication by event ID, signature checks.
- Feature parsers (lizard, tree-sitter feature queries, Semgrep, diff checks)
  stay in Python inside `scorer`, shared with `pipelines/`.
- If profiling later shows blame or history features are too slow in Python,
  the Go miner may precompute raw tables for them. It never computes the
  feature itself.

## Options not taken

| Option | Why not |
| --- | --- |
| Everything in Python | Works, but the miner is the hardest concurrency problem in the system and the team gains no Go depth |
| Feature parsers in Go | Training would need the same Go code, or features drift between training and serving |
| Go parser service called over the network | New contract, new deploy, and another version to pin, for no accuracy gain |
| Put the miner or compiler in `intake` | `intake` must stay tiny and answer within 10 seconds |
| Config merging in both `scorer` and `app` | Two implementations of merge rules in two languages would diverge |

## Consequences

- Services grow from four to five, then six: `miner` and `compiler`. The root
  README architecture table and layout are updated when each lands.
- `contracts/mined_history.schema.json` is the only interface between the Go
  miner and Python. Changing a table column is a contract change.
- `miner_version` becomes part of the feature extractor version pinned in
  `model_version`.
- #10 adds Go build and test jobs for the new services to CI, and the Job
  templates to `infra/k8s/`.
- Workload: the miner is on the week 3 to 5 data critical path. If it slips,
  pipelines fall back to a Python miner for the backtest repositories only.

## Revisit if

- The miner is not producing complete runs for the backtest repositories by
  the end of week 5.
- Any feature ends up computed in Go.
- The compiler adds latency to scoring that a cached Python loader would not.

## Open

- Parquet library for Go (for example `parquet-go`) and whether Python reads
  with pyarrow or DuckDB.
- Whether the salt for `author_email_hash` is per installation or per repo.
