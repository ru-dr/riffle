# 0025: Pooled tenants, per-tenant keys, and an install and uninstall workflow

- **Date:** 2026-11-25
- **Status:** Accepted
- **Owners:** #10 Deployment & CI/CD, with #8 GitHub App (to confirm, see Open)

## Context

The README defines a tenant as one GitHub App installation that shares the global model and has its own per-repo layer, data and metrics. No record says how tenants are kept apart in storage, or what happens to a tenant's data when the App is uninstalled. Every service is meant to keep one warm replica and scale out (the production architecture at `site/public/architecture/`), so a separate deployment per tenant multiplies that floor by the number of tenants. Deleting a tenant today would mean finding every row, file and backup that holds its data. `organization.outcome_sharing` in `contracts/org_config.schema.json` is off by default and is the only path for a tenant's data to leave.

## Decision

- **One pooled deployment serves every tenant.**
  - Every table row and object path carries `tenant_id`.
  - Postgres row-level security policies filter on `tenant_id`. Each service sets the tenant per connection (`SET app.tenant_id`) before it queries.
- **Each tenant has its own data key.**
  - The key is wrapped by a key-encryption key in the secrets vault (Vault Transit or the provider's KMS, chosen per cloud in Terraform).
  - It encrypts the tenant's objects on object storage: the score log (ADR 0024), training snapshots and backfill output.
  - Postgres rows rely on row-level security and disk encryption, not per-tenant keys.
- **The scoring path does not depend on the key store.** The scorer reads Postgres, which is not encrypted per tenant, so scoring continues while the vault is down. Writes that need a key, such as score log files, wait in the queue until the key is available.
- **On `installation.created`, a workflow:**
  - creates the tenant row;
  - generates its data key;
  - starts the history backfill from ADR 0001 on the lowest-priority queue lane;
  - turns scoring on.

  Every step is idempotent per installation ID.
- **On `installation.suspend`, scoring pauses and nothing is deleted.**
- **On `installation.deleted`, the workflow:**
  - stops scoring and drops cached installation tokens at once;
  - after a grace period, deletes the tenant's rows and its object storage prefix;
  - destroys its data key, which makes any copy of those objects unreadable.
- **The workflow runs on a durable workflow engine**, so a failed step resumes instead of leaving a half-deleted tenant.
- **A dedicated cell for a single tenant is not built now.** If one is needed, it is the same Terraform module with its own namespace and database, not a separate design.

## Options not taken

| Option | Why not |
| --- | --- |
| A cloud project or account per tenant | Multiplies the warm-replica floor, quotas and Terraform state by the number of tenants, and makes onboarding a provisioning job |
| A Postgres schema per tenant | Every migration runs once per tenant, and connection pools split per schema |
| No per-tenant keys | Deleting a tenant depends on finding every copy, backups included |
| Per-tenant keys on Postgres rows as well | Every query would need a decrypt, and scoring would stop whenever the key store is down |

## Consequences

- **Postgres migrations** add row-level security policies, and every service sets `app.tenant_id` per connection. Tests must cover reads across tenants.
- **`services/app`** handles the `installation` events and starts the workflow. A workflow engine joins the cluster platform.
- **Terraform** creates the key-encryption key and the vault policies.
- **Retention:** Postgres backups keep a deleted tenant's rows until those backups expire. Object storage copies are unreadable once the key is destroyed.
- **Opt-in exports stay:** outcomes already exported under `outcome_sharing` stay in the shared set, anonymised, and models already trained on them are not retrained. The uninstall flow must say so.
- **Invariants:**
  - **Tenant fairness:** in a pooled deployment it rests on the queue's per-tenant ordering keys and limits.
  - **Nothing leaves the installation:** the tenant's data stays put unless `outcome_sharing` is on.

## Revisit if

- A customer contract requires dedicated infrastructure.
- A test or incident shows a read across tenants.
- Key store latency or outages delay score log writes past their retention window.

## Open

- Length of the grace period before deletion.
- How long services cache a decrypted data key.
- Which workflow engine (for example Temporal or Argo Workflows).
- How long Postgres backups are kept.
- Owners: #10 Deployment & CI/CD and #8 GitHub App are a proposal; confirm them.
