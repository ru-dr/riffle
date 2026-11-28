# 0028: Keep tenant data keys in Postgres and encrypt score log files on the client

- **Date:** 2026-11-27
- **Status:** Proposed
- **Owners:** #10 Deployment & CI/CD, with #9 Inference (to confirm, see Open)

## Context

ADR 0024 says each tenant's score log files are encrypted with that tenant's key, and ADR 0025 says each tenant has a data key, wrapped by a key-encryption key and made by the install workflow. Neither says where the wrapped key is kept or how a file is encrypted with it. The install workflow is not built, and its engine is still open (ADR 0025). The score-log writer cannot write a file without a key, so until this is decided `score-log` messages only pile up in the queue. Terraform already creates the KMS key `tenant-kek` and lets the writer use it (`infra/terraform/envs/staging/09-identity.tf`).

## Decision

- **The wrapped data key is a row in Postgres:** table `tenant_keys` (`tenant_id`, `wrapped_key`, `kek_name`, `created_at`), in migration `infra/db/migrations/20261128000001_tenant_keys.sql`.
  - Row-level security like every table; services may read and insert, never update or delete; `riffle_readonly` gets no access.
  - It references `tenants` with `ON DELETE CASCADE`, so deleting a tenant deletes its key.
- **The key is a random 32-byte AES key, wrapped by KMS `tenant-kek`** with `riffle-tenant-data-key:<tenant_id>` as associated data, so a wrapped key only unwraps for its own tenant. The plain key exists only in memory, cached for 10 minutes (`KEY_CACHE_SECONDS`).
- **Whoever first needs a key makes it**, with `INSERT ... ON CONFLICT DO NOTHING` followed by a read, so writers that race end with the same key. Keys are made only for tenants whose status is `active` or `suspended`. The install workflow may make it earlier; the result is the same.
- **A file is encrypted on the client:** `RFLE1` (5 bytes), a 12-byte nonce, then the AES-256-GCM ciphertext and tag, with the object's name as associated data. A file moved to another tenant's or day's path does not decrypt.
- **The score-log writer** (`services/scorelog-writer`) is the first user: Parquet in the flat form of ADR 0012, under `score_log/tenant=<tenant_id>/date=<YYYY-MM-DD>/<HHMMSS>-<random>.parquet.enc`, one file per tenant and day per flush. Rows of a tenant that is being deleted or does not exist are dropped.

## Options not taken

| Option | Why not |
| --- | --- |
| Wrapped key as an object next to the tenant's files | Deleting the tenant's rows would not remove it; a second store to keep in step with `tenants` |
| Google customer-supplied keys (CSEK) on each object | Ties the file format to one cloud; ADR 0024 keeps the log readable on any S3-compatible storage |
| Parquet modular encryption | Every reader would need the same key-retrieval plug-in; one envelope around the file is simpler to read and to test |
| One KMS key per tenant, no data key | A KMS call for every file; KMS limits the number of keys and calls |
| Wait for the install workflow to make keys | The workflow engine is not chosen; score log rows would wait in the queue until it is built |
| Write the files unencrypted for now | Breaks ADR 0024 (Accepted) until fixed |

## Consequences

- New: `tenant_keys` and its migration; the score-log writer, its Argo CD Application (`infra/k8s/apps/staging/22-scorelog-writer.yaml`), CI job `scorelog-writer` and image; `database = true` for `scorelog-writer` in `09-identity.tf` and its grant in `infra/k8s/platform/db-migrate/grants.sql`.
- Readers of the score log (layer fits, `riffle_tenant_model_auc`, the drift monitor) need Postgres and `tenant-kek` decrypt access to read a file: `python -m scorelog_writer read <gs://...>` decrypts one.
- **Deleting a tenant deletes its key** with its `tenants` row, which makes every copy of its files unreadable, as ADR 0025 requires. Postgres backups keep the wrapped key until they expire, but it is useless without `tenant-kek`.
- KMS or Postgres being down delays score log files, never scores (ADR 0025): the rows wait in the queue, and after five deliveries in the dead letter, kept seven days.
- New metrics: `riffle_score_log_rows_total{result}`, `riffle_score_log_files_total`, `riffle_score_log_write_failures_total`, `riffle_score_log_buffered_rows`, `riffle_score_log_flush_seconds`.
- Invariants: nothing leaves the installation (keys and files stay in its own Postgres, KMS and bucket); the scoring path does not use keys.

## Revisit if

- The install workflow is built and the team wants it to be the only place keys are made.
- Another cloud without a KMS is targeted (ADR 0025 names Vault Transit as the alternative to wrap with).
- Training snapshots and backfill output (ADR 0025) need a different file format from the score log's.

## Open

- Owners: #10 Deployment & CI/CD and #9 Inference are a proposal; confirm them.
- How long a decrypted key may stay cached (ADR 0025 leaves it open; 10 minutes is the default here).
- Rotating `tenant-kek`: old wrapped keys keep their `kek_name`, but re-wrapping them is not built.
