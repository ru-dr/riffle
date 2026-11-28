# team-access

What people on the team may do in the project, as code. Every member (a user or, better, a
Google group) gets the same roles: enough to run and debug the platform from a laptop, not to
change IAM or delete resources. Applying Terraform needs more, and is done by the project owner
or by CI on `main`.

| Role | Lets a member |
| --- | --- |
| `container.developer` | Use `kubectl`: pods, logs, Kubernetes Secrets, `port-forward` |
| `secretmanager.secretAccessor`, `secretmanager.viewer` | List and read Secret Manager secrets |
| `cloudsql.client`, `cloudsql.instanceUser` | Log in to Postgres with their own Google token, through the `db-proxy` pod and `port-forward` (the database has no public IP; also add them to `db_people`) |
| `monitoring.viewer`, `logging.viewer` | Metrics, alerts and logs |
| `pubsub.viewer` | See topics, subscriptions and dead letters |
| `artifactregistry.reader` | Pull images |
| `storage.objectViewer` on the state bucket | `terraform output`; the state holds generated secret values, so only the team gets this |

| Input | |
| --- | --- |
| `members` | `user:<email>` or `group:<email>` |
| `roles` | The list above by default |
| `state_bucket` | The Terraform state bucket |
