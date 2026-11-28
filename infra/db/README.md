# db

Postgres tables shared by the services, as plain SQL migrations applied by
[dbmate](https://github.com/amacneil/dbmate). Plain SQL keeps them independent of the
services' languages (Go, Python, TypeScript). On GCP they run against Cloud SQL as a
Kubernetes Job before each deploy.

| Table | Holds | Key |
| --- | --- | --- |
| `tenants` | One row per GitHub App installation, with its status (active, suspended, deleting) | `tenant_id` (the installation id) |
| `repos` | The repositories each installation gave access to | `tenant_id`, `repo_id` |
| `tenant_keys` | Each tenant's data key, wrapped by KMS `tenant-kek`; encrypts its files (ADR 0028). Services may read and insert, never change; people cannot read it | `tenant_id` |
| `score_results` | One row per score: the outcome probabilities, `rank_score` and band, model and miner versions; append-only. `features` stays null: the model's inputs go to the score log in object storage, not Postgres (ADR 0024) | `delivery_id` |

## Invariants

- **Every table carries `tenant_id` and has forced row-level security** (ADR 0025). The
  policy is `tenant_id = riffle_current_tenant()` for reads and writes. A service sets the
  tenant per transaction with `SET app.tenant_id = '<installation id>'`; with nothing set it
  sees no row and can write none.
- **Services connect as a login in the role `riffle_app`**, which cannot bypass row-level
  security and owns nothing. The migration creates the role if it is missing; Terraform
  creates the login and grants it the role. Never connect a service as a superuser: a
  superuser skips every policy.
- **People get `riffle_readonly`**, never a write role: it reads every table and is still
  bound by row-level security (`SET app.tenant_id` first). It is granted through `db_people`
  in Terraform (`database_roles = ["riffle_readonly"]`), after the migration that creates it
  has run.
- **`score_results` is append-only for services** (no `UPDATE` or `DELETE` grant). A rescore
  is a new delivery and a new row. Its key, `delivery_id`, makes scoring idempotent.
- **A score is either ranked or unranked with a reason, never half.** `rank_score`,
  `rank_band` and `model_version` are all set, or `rank_score` and `rank_band` are null with
  `unranked_reason` set (fail open). Null means "not ranked", never 0. `risk_score` stays null
  until a `label_bad` model is served (migration `20261127000002`).

## Adding a migration

Name it `<UTC timestamp>_<what_it_does>.sql` (for example `20261201120000_outcomes.sql`),
with one `-- migrate:up` and one `-- migrate:down` section. Never edit a migration that has
run anywhere shared; add a new one. A new table needs `tenant_id`, `ENABLE` and `FORCE ROW
LEVEL SECURITY` and the `tenant_isolation` policy.
