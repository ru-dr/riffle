# k8s

What runs inside the GKE cluster, deployed by Argo CD from this folder (GitOps): a change
merged to `main` is applied to the cluster by Argo CD, never by hand. Terraform
(`infra/terraform/envs/staging-bootstrap`) only installs Argo CD and creates the namespaces and
their `riffle-platform-settings` ConfigMap.

Training jobs run under a bounded pool — that bound is a product invariant,
not a cost tweak.

## Layout

```
infra/k8s/
├── apps/staging/        one Argo CD Application per component (app of apps), numbered in deploy order
└── platform/            what the Applications that point here deploy (each folder is a Kustomization)
    ├── cluster-policies/   priority classes, default requests, quotas, network rules
    ├── identity/           the Kubernetes service accounts of the Riffle services
    ├── secrets/            a SecretStore and ExternalSecret per service
    ├── training-queues/    the Kueue queue training Jobs go through
    ├── gateway/            Gateway, routes, Cloud Armor and health checks on the backends
    └── db-migrate/         the Job that applies infra/db/migrations and grants database roles
```

## Deploy order (sync waves)

| Wave | Application | What | Source |
| --- | --- | --- | --- |
| -3 | `cluster-policies` | Priority classes (serving > workers > batch), default requests, quotas on workers and ml, default-deny ingress plus the allowed paths | `platform/cluster-policies` |
| -3 | `identity` | 12 Kubernetes service accounts, each annotated with its Google account | `platform/identity` |
| -2 | `external-secrets` | External Secrets Operator 2.15.0 | Helm chart |
| -2 | `keda` | KEDA 2.21.1, its operator as the `keda` Google account | Helm chart |
| -2 | `kueue` | Kueue 0.20.8 (OCI chart) | Helm chart |
| -1 | `secrets` | One SecretStore and ExternalSecret per service that needs secrets | `platform/secrets` |
| -1 | `training-queues` | Spot flavour, `training` ClusterQueue (16 CPU, 64 Gi), LocalQueue in `riffle-ml` | `platform/training-queues` |
| 0 | `gateway` | The load balancer on the fixed IP; `/webhook` and `/healthz` to intake, `/app` to the dashboard; Cloud Armor on both | `platform/gateway` |
| 1 | `db-migrate` | Migrations and grants, on every sync | `platform/db-migrate` |
| 2 | `loki`, `tempo` | Logs and traces, stored in their buckets | Helm charts |
| 3 | `alloy`, `grafana` | Log and trace collection; dashboards | Helm charts |

Argo CD waits for each wave to be healthy before the next (configured in
`infra/terraform/envs/staging-bootstrap/values/argocd.yaml`).

## Namespaces

Made by Terraform, each with the `riffle-platform-settings` ConfigMap (Cloud SQL connection
name, Redis host and port, topic, subscription and bucket names, the tenant key id, the image
registry). Workloads read it with `envFrom: - configMapRef: { name: riffle-platform-settings }`,
so no manifest hard-codes an environment's resource names.

| Namespace | Runs | Priority class |
| --- | --- | --- |
| `riffle-gateway` | The Gateway | — |
| `riffle-serving` | intake, scorer, explainer, app-github, app-dashboard | `riffle-serving` |
| `riffle-workers` | outcome-labeler, scorelog-writer, tenant-workflow, db-migrate | `riffle-workers` |
| `riffle-ml` | retrain, trainer, mlflow | `riffle-batch` (training), `riffle-workers` |
| `observability` | Loki, Tempo, Alloy, Grafana | — |

## What a service chart must do (when the services are written)

- Run as its service account from `platform/identity` (`serviceAccountName: <service>`); never
  create its own.
- Label pods `app.kubernetes.io/name: <service>`: the network rules match on it.
- Read settings from `riffle-platform-settings` and secrets from the Secret `<service>-secrets`.
- Connect to Postgres through a Cloud SQL Proxy sidecar with `--auto-iam-authn --private-ip`,
  as user `riffle-staging-<service>@riffle-infra-platform.iam` (no password); set
  `app.tenant_id` per transaction.
- Expose metrics on a port named `metrics` and send traces (OTLP) to `alloy.observability.svc:4317`.
- Training Jobs: label `kueue.x-k8s.io/queue-name: training`, priority class `riffle-batch`.
- Set honest, small resource requests: Autopilot bills them.

## Reaching the tools (no public address yet)

| Tool | Command |
| --- | --- |
| Argo CD | `kubectl -n argocd port-forward svc/argocd-server 8080:443` (user `admin`, password in Secret `argocd-initial-admin-secret`) |
| Grafana | `kubectl -n observability port-forward svc/grafana 3000:80` (user `admin`, password in Secret `grafana`) |

Credentials for `kubectl`: `gcloud container clusters get-credentials riffle-staging --region us-east4 --dns-endpoint`.

## Adding things

- A migration: add the file to `infra/db/migrations` **and** to `platform/db-migrate/kustomization.yaml`
  (Kustomize cannot glob).
- A service that uses the database: add its user to `platform/db-migrate/grants.sql`, as well
  as `database = true` in `infra/terraform/envs/staging/09-identity.tf`.
- A secret for a service: grant it in `09-identity.tf`, then add it to that service's
  ExternalSecret in `platform/secrets`.

## Troubleshooting

| Symptom | Look at |
| --- | --- |
| An Application stays OutOfSync or Degraded | Argo CD UI: the resource's events |
| `SecretSyncedError` on an ExternalSecret | The secret has no value yet (filled by hand), or the service lacks access in `09-identity.tf` |
| `db-migrate` fails | Its pod's logs: `migrate` (SQL), `cloud-sql-proxy` (login), `grants` (roles) |
| `/webhook` returns 403 | Cloud Armor: the caller is not in GitHub's hook ranges (`github_hook_ranges`) |
| Route shows unresolved backends | The service's Service does not exist yet |
