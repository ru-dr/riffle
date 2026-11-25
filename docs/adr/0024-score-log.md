# 0024: Log every score with the features the scorer saw

- **Date:** 2026-11-25
- **Status:** Proposed
- **Owners:** #9 Inference, with #2 Training (to confirm, see Open)

## Context

Training data today comes from the repo miner, which rebuilds each PR's features after the fact. ADR 0010 shows how hard that is to get right: features computed from a PR's final state leaked the label, and recovering the PR as it opened needs force-push history that is not always readable (`open_snapshot` = `approx` or `final`). Once the scorer runs, no record keeps the features it actually used, so a repo's layer is trained on rebuilt features while it serves on live ones. `riffle_tenant_model_auc` needs live scores joined with mature outcomes, and nothing stores those scores with their inputs. ADR 0014 (Proposed) rules out a feature store and keeps features on one code path in `libs/features/`.

## Decision

- **The scorer appends one row to a score log for every score it writes:** stage 1, the stage 2 rescore, and every later rescore.
- **Each row holds:**
  - `delivery_id`, `tenant_id`, `repo`, `pr_number`, `head_sha`;
  - `trigger` (the `PrEvent` action);
  - `scored_at`;
  - `model_version` and the feature extractor version;
  - the full feature vector, in the flat form of ADR 0012;
  - the score, the band and `layer_weight`.
- **Rows are written after the `ScoreResult` is committed**, through a `score-log` topic on the message queue. A writer job batches them into Parquet. Logging is never on the scoring path: if the topic is unavailable the score still posts, and `riffle_score_log_dropped_total` counts the loss.
- **Storage:**
  - Parquet on object storage, under `score_log/tenant=<tenant_id>/date=<YYYY-MM-DD>/`, read with DuckDB as ADR 0014 proposes.
  - Each tenant's files are encrypted with that tenant's key (ADR 0025).
  - Rows are never rewritten.
- **New contract `contracts/score_log.schema.json`**, version 1.0, in flat form (ADR 0012). It gets its regenerated `contracts/REFERENCE.md` and a row in `docs/contracts.md`.
- **What reads the log:**
  - **Per-repo layer fits** train on the log's rows from `trigger` = `opened`, joined with mature outcomes.
  - **Global training** uses log rows only from tenants with `organization.outcome_sharing.enabled`.
  - **`riffle_tenant_model_auc`** is computed from the log joined with mature labels.
  - **The drift monitor** reads it.
  - **The promotion gate** (ADR 0014) may use it as a time-ordered holdout.
- **The miner remains the source** for history from before install (the backfill in ADR 0001) and for any feature added after a row was logged. A logged row is used only with the extractor version it records.

## Options not taken

| Option | Why not |
| --- | --- |
| Keep rebuilding features with the miner only | The model trains on different inputs from the ones it serves on, and each rebuild risks the leaks ADR 0010 fixed |
| Store the log in Postgres | Feature vectors for every score would grow the transactional store without bound; the log is analytical and append-only |
| A feature store (for example Feast) | ADR 0014 rules one out; the log is a plain append-only record, not a serving layer |
| Send rows to Loki | Loki holds log lines for days and cannot be queried as a table for training |
| A managed warehouse (for example BigQuery) | Ties the stack to one cloud; Parquet on S3-compatible storage works anywhere |

## Consequences

- **`scorer`** publishes to a new `score-log` topic. A new writer job turns the topic into Parquet files.
- **New contract** `contracts/score_log.schema.json`, plus `contracts/REFERENCE.md` and `docs/contracts.md` in the same PR. CODEOWNERS approval is needed for `contracts/`.
- **`pipelines/training/`** gains a loader for the log beside `frames.py`. Layer fits switch to it once a tenant has enough rows; until then they train on backfilled miner rows.
- **New metrics:** `riffle_score_log_rows_total` and `riffle_score_log_dropped_total`.
- **Storage grows** with every score. The log holds author-history features, so it is sensitive data: it is encrypted per tenant and purged when the tenant leaves (ADR 0025).
- **Invariants:**
  - **Point-in-time scoring gets stronger:** training uses what was known when the score was made.
  - **Nothing leaves the installation:** the log stays in the installation's own storage, and only the opt-in `outcome_sharing` path ever moves it.

## Revisit if

- The log for one installation no longer fits a single DuckDB process.
- Layer AUC trained on the log is not better than layer AUC trained on rebuilt features over the same period, so the cost buys nothing.
- A feature store is adopted for another reason.

## Open

- How long rows are kept while a tenant stays installed.
- Whether stage 2 rescore rows are used in training, or only `opened` rows.
- Whether free-text features (such as titles) are logged, or only numeric ones.
- Owners: #9 Inference and #2 Training are a proposal; confirm them.
