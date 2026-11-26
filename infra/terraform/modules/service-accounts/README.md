# service-accounts

One Google service account per service, linked to that service's Kubernetes service account
through Workload Identity, with only the permissions listed for it. No service account key is
ever created: pods get short-lived tokens.

The list of services and their permissions lives in the environment (`envs/staging/09-identity.tf`).
That file answers "what may service X touch?".

| File | Holds |
| --- | --- |
| `main.tf` | The accounts and the Workload Identity link (`<namespace>/<ksa>` may act as the account) |
| `permissions.tf` | Every grant, one resource per kind; keys are `<service>/<thing>` so plan output names both |

| Field per service | Grants |
| --- | --- |
| `namespace`, `ksa` | Which Kubernetes service account may act as it (`ksa` defaults to the service's name) |
| `publishes` | `roles/pubsub.publisher` on each topic |
| `subscribes` | `roles/pubsub.subscriber` on each subscription |
| `buckets` | The given role on each bucket: `objectViewer` (read), `objectCreator` (add only), `objectUser` (read, write, delete) |
| `secrets` | `roles/secretmanager.secretAccessor` on each secret (External Secrets reads it as this service) |
| `tenant_kek` | `roles/cloudkms.cryptoKeyEncrypterDecrypter` on the tenant key-encryption key only |
| `database` | `roles/cloudsql.client` + `roles/cloudsql.instanceUser`, and a database login bound by row-level security |
| `database_superuser` | The same, with `cloudsqlsuperuser`; allowed for one service only (the migration Job) |
| `project_roles` | Project-wide roles, only where nothing narrower exists (KEDA reading metrics) |

Every grant is on one resource, except the two Cloud SQL roles (Cloud SQL has no per-instance
permissions) and `project_roles`.

A misspelled topic, subscription, bucket or secret fails at `plan` with "Invalid index".

| Output | Used by |
| --- | --- |
| `emails` | Reference |
| `kubernetes_annotations` | Helm values: each Kubernetes service account gets `iam.gke.io/gcp-service-account` (phase 12) |
| `database_users` | The cloud-sql module's `iam_users` (merged with `db_people` in `04-database.tf`) |
