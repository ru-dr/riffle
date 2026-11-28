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
├── charts/riffle-service/  the shared Helm chart every Riffle service is deployed with
└── platform/            what the Applications that point here deploy (each folder is a Kustomization)
    ├── cluster-policies/   priority classes, default requests, quotas, network rules
    ├── identity/           the Kubernetes service accounts of the Riffle services
    ├── secrets/            a SecretStore and ExternalSecret per service
    ├── training-queues/    the Kueue queue training Jobs go through
    ├── gateway/            Gateway, routes, Cloud Armor and health checks on the backends
    ├── db-migrate/         the Job that applies infra/db/migrations and grants database roles
    ├── mlflow/             the model registry (Cloud SQL database mlflow, files in the models bucket)
    └── training/           the weekly global training CronJob, through Kueue
```

## Deploy order (sync waves)

| Wave | Application | What | Source |
| --- | --- | --- | --- |
| -3 | `cluster-policies` | Priority classes (serving > workers > batch), default requests, quotas on workers and ml, default-deny ingress plus the allowed paths | `platform/cluster-policies` |
| -3 | `identity` | 14 Kubernetes service accounts, each annotated with its Google account | `platform/identity` |
| -2 | `external-secrets` | External Secrets Operator 2.15.0 | Helm chart |
| -2 | `keda` | KEDA 2.21.1, its operator as the `keda` Google account | Helm chart |
| -2 | `kueue` | Kueue 0.20.8 (OCI chart) | Helm chart |
| -1 | `secrets` | One SecretStore and ExternalSecret per service that needs secrets | `platform/secrets` |
| -1 | `training-queues` | Spot flavour, `training` ClusterQueue (16 CPU, 64 Gi), LocalQueue in `riffle-ml` | `platform/training-queues` |
| 0 | `gateway` | The load balancer on the fixed IP; `/webhook` and `/healthz` to intake, `/app` to the dashboard; Cloud Armor on both | `platform/gateway` |
| 1 | `db-migrate` | Migrations and grants, on every sync | `platform/db-migrate` |
| 2 | `loki`, `tempo` | Logs and traces, stored in their buckets | Helm charts |
| 2 | `mlflow` | MLflow 3.18 with a Cloud SQL Proxy sidecar; `http://mlflow.riffle-ml.svc:5000` | `platform/mlflow` |
| 2 | `training` | Weekly global training CronJob (suspended, see Training) | `platform/training` |
| 4 | `intake` | The webhook front door (`services/intake`), at least 1 warm pod, HPA on CPU | `charts/riffle-service` |
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
| `riffle-workers` | pr-miner, outcome-labeler, scorelog-writer, tenant-workflow, db-migrate | `riffle-workers` |
| `riffle-ml` | retrain, trainer, mlflow | `riffle-batch` (training), `riffle-workers` |
| `observability` | Loki, Tempo, Alloy, Grafana | — |

## Deploying a service: the shared chart

Every Riffle service is deployed with `charts/riffle-service`, from an Application in
`apps/staging` (see `20-intake.yaml`) that sets only what differs. The chart does what the
platform expects, so a service cannot forget it:

| The chart | Value |
| --- | --- |
| Runs the pod as the service account from `platform/identity` (`serviceAccountName: <name>`), never its own | `name` |
| Labels pods `app.kubernetes.io/name: <name>` (the network rules match on it) | `name` |
| Reads `riffle-platform-settings`, and `<name>-secrets` when asked | `secrets: true` |
| Adds the Cloud SQL Proxy sidecar and sets `DB_HOST`, `DB_PORT`, `DB_USER` (no password); the service sets `app.tenant_id` per transaction | `cloudSql.enabled: true` |
| Makes a Service for the port, with readiness and liveness probes | `port`, `probes` |
| Lets Managed Prometheus scrape `/metrics` on the port named `metrics` (`PodMonitoring`) | `metricsPort` |
| Sets `OTEL_EXPORTER_OTLP_ENDPOINT` to Alloy, for traces | `otlpEndpoint` |
| Scales on CPU (HPA) or on Pub/Sub backlog (KEDA), never below 1 warm pod | `autoscaling.hpa` or `autoscaling.keda` |
| Keeps a pod up during upgrades (PodDisruptionBudget), rolls out without dropping below the current count, spreads pods over zones | `pdb` |
| Runs as non-root with a read-only filesystem (a writable `/tmp`) | — |

The chart refuses to render without `name`, `image.repository` and `image.tag`, with both HPA
and KEDA, or with KEDA but no subscription.

To deploy a new build, put the tag from the `images` workflow's summary in the
Application's `image.tag`. Services still to write follow `services/<name>/README.md`.

## Training

`platform/training` runs the outcome model container (`infra/docker/outcome-training`, the same
one that runs on a laptop) every Sunday at 02:00 UTC as a Job in the Kueue queue `training`:
fetch the data release from the snapshots bucket, train and run the gates, then copy the
results to `gs://<models bucket>/runs/<job name>/`. A failed gate stops the run at once; a
Spot preemption starts it again.

It ships suspended. To turn it on:

1. The `images` workflow (`.github/workflows/images.yml`) builds the training image
   (`outcome-training`) and, on `main`, pushes it to Artifact Registry as `sha-<commit>`. Put
   that tag, shown in the run's summary, in `platform/training/kustomization.yaml` (`newTag`).
2. A data release is copied to `gs://<snapshots bucket>/releases/dataset_merged.parquet`
   (the `DATA_RELEASE` setting in the CronJob).
3. `suspend: false` in `platform/training/global-training.yaml`.

To start one run by hand: `kubectl -n riffle-ml create job --from=cronjob/global-training train-now`.

Not here yet: registering a passing run in MLflow from the cluster (`register_model.py` needs
`mlflow` in the training image), the nightly repo model fits and the triggers on new labels or
drift (their code does not exist yet).

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
