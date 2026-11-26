# db

Postgres tables shared by the services, as plain SQL migrations applied by
[dbmate](https://github.com/amacneil/dbmate). Plain SQL keeps them independent of the
services' languages (Go, Python, TypeScript). On GCP they run against Cloud SQL as a
Kubernetes Job before each deploy.

| Table | Holds | Key |
| --- | --- | --- |
| `tenants` | One row per GitHub App installation, with its status (active, suspended, deleting) | `tenant_id` (the installation id) |
| `repos` | The repositories each installation gave access to | `tenant_id`, `repo_id` |
| `score_results` | One row per score; append-only | `delivery_id` |

## Invariants

- **Every table carries `tenant_id` and has forced row-level security** (ADR 0025). The
  policy is `tenant_id = riffle_current_tenant()` for reads and writes. A service sets the
  tenant per transaction with `SET app.tenant_id = '<installation id>'`; with nothing set it
  sees no row and can write none.
- **Services connect as a login in the role `riffle_app`**, which cannot bypass row-level
  security and owns nothing. The migration creates the role if it is missing; Terraform
  creates the login and grants it the role. Never connect a service as a superuser: a
  superuser skips every policy.
- **`score_results` is append-only for services** (no `UPDATE` or `DELETE` grant). A rescore
  is a new delivery and a new row. Its key, `delivery_id`, makes scoring idempotent.
- **A score is either ranked or unranked with a reason, never half.** `risk_score`,
  `rank_band` and `model_version` are all set, or all null with `unranked_reason` set (fail
  open). Null means "not ranked", never 0.

## Adding a migration

Name it `<UTC timestamp>_<what_it_does>.sql` (for example `20261201120000_outcomes.sql`),
with one `-- migrate:up` and one `-- migrate:down` section. Never edit a migration that has
run anywhere shared; add a new one. A new table needs `tenant_id`, `ENABLE` and `FORCE ROW
LEVEL SECURITY` and the `tenant_isolation` policy.
