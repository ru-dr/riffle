# cloud-sql

Postgres for the services (database `riffle`, tables in `infra/db/migrations`) and the model
registry (database `mlflow`). Diagram: "Postgres" and the Model registry's metadata.

| Setting | Why |
| --- | --- |
| Private IP for pods; optional connector-only public IP (`connector_public_ip`) | Pods use the private IP. The public IP has no authorised networks, so with the connector required the only way in is the Cloud SQL Proxy with a Google login: how the team connects from a laptop |
| `connector_enforcement = REQUIRED` | Every connection goes through the Cloud SQL connector (Auth Proxy sidecar or a language connector): TLS and a Google identity on each login |
| IAM logins only, no passwords | Services and the migration Job log in as their Google service accounts. The built-in `postgres` user is never given a password, so it cannot log in |
| Edition `ENTERPRISE` | Postgres 16+ would otherwise default to Enterprise Plus, at several times the price |
| `availability_type` input | `ZONAL` in staging; `REGIONAL` in prod for a standby in a second zone with automatic failover |
| Daily backups + point-in-time recovery (7 days), stored in the `us` multi-region | Restore to any second in the last week; backups survive even if the instance's region fails |
| Random name suffix | Cloud SQL reserves a deleted instance's name for about a week; the suffix lets staging be destroyed and rebuilt |
| Query insights, slow-query log (> 500 ms), lock-wait log | Find slow queries |
| Sunday 04:00 UTC maintenance | Inside the weekend window the cluster uses |

## Logins

`iam_users` lists the Google identities that may log in: service accounts (filled in phase 9) and people (`db_people` in the environment, for debugging). A service account's database user name is its email without `.gserviceaccount.com`; a person's is their full email.

| Account | Database role | Why |
| --- | --- | --- |
| The migration Job's | `cloudsqlsuperuser` | Creates tables, the `riffle_app` role and row-level security |
| A team member's | none by default | Can connect; give a role only when needed |
| Each service's | none here; `riffle_app` from the migration Job | `riffle_app` is created by the first migration, so it is granted right after, by the `grants` step of `infra/k8s/platform/db-migrate` (`grants.sql`) |

Services must never get `cloudsqlsuperuser`: it is not bound by row-level security.

| Output | Used by |
| --- | --- |
| `connection_name` | The Cloud SQL connector in every pod (`project:region:instance`) |
| `instance_name`, `private_ip`, `databases`, `iam_user_names` | Reference and the migration Job |
