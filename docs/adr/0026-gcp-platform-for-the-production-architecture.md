# 0026: Run the production architecture on GCP

- **Date:** 2026-11-26
- **Status:** Proposed
- **Owners:** #10 Deployment & CI/CD (to confirm, see Open)

## Context

The production architecture (`site/public/architecture/`) is drawn for any cloud: a Kubernetes cluster, a message queue, Postgres, Redis, object storage, a key vault, an edge with a WAF, and Prometheus, Loki, Tempo and Grafana. It does not say which cloud services fill those boxes. `infra/terraform` held only the AIDev ingestion base (`foundation`) and the store VM (ADR 0018), and `infra/k8s` held only a README. ADR 0024 (score log) and ADR 0025 (pooled tenants, per-tenant keys, install workflow) need storage, a key-encryption key and namespaces that did not exist. The team has GCP credit through the Education account and a single region, `us-east4`, already in use. The services themselves are not written yet.

## Decision

- **Cloud and layout.** GCP project `riffle-infra-platform`, region `us-east4`, one environment (`staging`) for now. Everything outside the cluster is Terraform in `infra/terraform`: one module per kind of resource in `modules/`, and two roots: `envs/staging` (cloud resources, one numbered file per phase) and `envs/staging-bootstrap` (namespaces, settings and Argo CD, applied after the cluster exists). Everything inside the cluster is deployed by Argo CD from `infra/k8s` (app of apps, sync waves). The code is written and validated; nothing is applied yet.
- **Diagram boxes to GCP:**

  | Diagram box | GCP | Where |
  | --- | --- | --- |
  | Kubernetes cluster | GKE Autopilot, regional, private nodes, control plane reached through its IAM-checked DNS endpoint | `modules/gke-autopilot` |
  | NAT gateway | Cloud NAT with one reserved egress IP | `modules/network` |
  | Message queue | Pub/Sub, topics and subscriptions from `infra/pubsub/topics.json`, a dead-letter topic and hold subscription per topic, messages stored only in the region | `modules/pubsub` |
  | Postgres | Cloud SQL Postgres 17, Enterprise edition, private IP for pods plus a public IP with no authorised networks for the team's Cloud SQL Proxy, connector required, IAM logins only (no passwords), backups with 7 days of point-in-time recovery in the `us` multi-region | `modules/cloud-sql` |
  | Redis | Memorystore Redis 7.2, AUTH and TLS, no persistence | `modules/memorystore` |
  | Object storage, Score log | Cloud Storage buckets `score-log`, `models`, `snapshots`, `loki`, `tempo`, public access blocked, lifecycle rules | `modules/storage-buckets` |
  | Key vault | Cloud KMS key `tenant-kek` (the key-encryption key of ADR 0025, rotated every 90 days) and Secret Manager | `modules/kms`, `modules/secrets` |
  | Pod identity | One Google service account per service with Workload Identity and grants on single resources; no service account keys | `modules/service-accounts`, `envs/staging/09-identity.tf` |
  | CI/CD, Container registry | Artifact Registry with immutable tags; GitHub Actions through Workload Identity Federation (images from any branch, Terraform from `main` only) | `modules/artifact-registry`, `modules/github-oidc` |
  | Load balancer, WAF | Global external Application Load Balancer created by the GKE Gateway on a reserved IP; Cloud Armor allows `/webhook` only from GitHub's hook ranges, OWASP rules in preview, a per-IP rate limit | `modules/edge`, `infra/k8s/platform/gateway` |
  | Prometheus, Alertmanager, Uptime checks, On-call | Google Managed Prometheus; alerting, uptime check and notification channels in Cloud Monitoring | `modules/monitoring` |
  | Loki, Tempo, Grafana, Alloy | In the cluster from Helm charts, storing in their buckets; Alloy reads pod logs through the Kubernetes API | `infra/k8s/apps/staging` |
  | Model registry, Global training | MLflow 3.18 in the cluster (database `mlflow`, files in the `models` bucket); a weekly CronJob runs `infra/docker/outcome-training` through the Kueue queue on Spot | `infra/k8s/platform/mlflow`, `infra/k8s/platform/training` |
  | Cluster platform | Argo CD, External Secrets, KEDA, Kueue, namespaces, priority classes, quotas, default-deny network rules | `envs/staging-bootstrap`, `infra/k8s` |

- **Settings reach pods from Terraform, not from manifests.** Each Riffle namespace has a `riffle-platform-settings` ConfigMap written by `envs/staging-bootstrap` from `envs/staging`'s outputs (Cloud SQL connection name, Redis host, topic, subscription and bucket names, key id, registry).
- **Database roles are granted by the migration Job.** `infra/k8s/platform/db-migrate` applies `infra/db/migrations` with dbmate, then grants `riffle_app` to each service's IAM user (`grants.sql`). Only the migration Job has `cloudsqlsuperuser`.
- **People get access through Google identities, granted in code** (`modules/team-access`): kubectl, Secret Manager, the database through the Cloud SQL Proxy with the read-only role `riffle_readonly`, metrics, logs and the Terraform state. Every secret is in Secret Manager, including Grafana's admin password.
- **Staging is sized small:** zonal Cloud SQL (`db-custom-1-3840`), basic Redis (1 GB), deletion protection off. Prod sets `REGIONAL` and `STANDARD_HA` and turns deletion protection on.
- **No domain yet,** so the load balancer serves HTTP on its fixed IP.
- **ADR 0018 is superseded:** its Terraform root was removed when `infra/terraform` was restarted. The store VM can still be made with `infra/docker/riffle-data/gcp/up.sh`.

## Options not taken

| Option | Why not |
| --- | --- |
| GKE Standard | Node pools to size and patch with no gain at about 15 small services; Autopilot bills per pod and supports Spot, GPUs and every add-on used |
| Cloud Run for the services | The architecture is a Kubernetes design with warm replicas, KEDA, Kueue and in-cluster observability; Cloud Run would need a second deployment model |
| NATS JetStream or Kafka in the cluster | A queue to run and patch; the architecture allows a managed queue behind the same contract |
| Postgres and Redis in the cluster | Backups, failover and patching would be ours |
| A second region | Roughly doubles the cost; zone failure is already covered by a regional cluster and, in prod, regional Cloud SQL and Redis |
| A cloud project per tenant | Ruled out by ADR 0025 |
| Running our own Alertmanager | Cloud Monitoring evaluates PromQL over Managed Prometheus and Pub/Sub metrics with nothing to run |
| Service account keys in GitHub secrets | Long-lived keys that can leak; Workload Identity Federation gives each run a one-hour token |
| A control-plane IP allow-list | Has to track every CI runner and laptop; the DNS endpoint checks Google IAM instead |
| Terraform managing workloads in the cluster | Two tools would own the same objects; Terraform stops at Argo CD |

## Consequences

- New: `infra/terraform/modules/*` (14 modules), `envs/staging`, `envs/staging-bootstrap`, `infra/k8s/apps/staging`, `infra/k8s/platform/*`, `infra/pubsub/topics.json`, `infra/db/migrations`. Removed: `infra/terraform/foundation` and `infra/terraform/store-vm`.
- The CI job `terraform` validates both roots with Terraform 1.9.
- The `images` workflow (`.github/workflows/images.yml`) builds the images on pull requests and, on `main`, pushes them to Artifact Registry tagged `sha-<commit>`, logging in through Workload Identity Federation. It needs three repository variables from the Terraform outputs; until they are set it only builds.
- Every service is deployed with the shared chart `infra/k8s/charts/riffle-service`, which runs it as its service account from `platform/identity`, labels pods `app.kubernetes.io/name`, reads `riffle-platform-settings` and `<service>-secrets`, adds the Cloud SQL Proxy sidecar with IAM login when asked, and has Managed Prometheus scrape the port named `metrics`. The first is intake (`infra/k8s/apps/staging/20-intake.yaml`).
- Adding a migration also means adding it to `platform/db-migrate/kustomization.yaml`; adding a database service also means adding it to `grants.sql`.
- The hosted deployment depends on GCP; the services do not. Their contracts, the topic list and the migrations stay cloud-neutral, so a self-hosted installation can fill the same boxes with other parts.
- Invariants:
  - **Nothing leaves the installation:** messages, secrets and keys are stored only in `us-east4`. Cloud SQL backups are kept in the `us` multi-region, outside the region but inside the same project.
  - **Tenant fairness:** Pub/Sub has no per-tenant fair share. ADR 0025 rests fairness on per-tenant ordering keys and limits; the scorer must enforce a per-tenant limit itself.
  - **Training under backpressure:** a Kueue ClusterQueue with a fixed CPU and memory quota.
  - **The LLM is never on the scoring path:** not affected.
  - **Riffle never blocks merges, nothing skips review, scores are point-in-time:** not affected.

## Revisit if

- A customer requires the hosted service on another cloud, or self-hosting needs a second set of Terraform modules.
- Autopilot's per-pod billing for the always-on pods costs more than the same pods on GKE Standard nodes, measured over a month.
- GitHub's hook ranges grow past 10 entries (one Cloud Armor rule's limit).
- Pub/Sub cannot keep one tenant from delaying others even with a per-tenant limit in the scorer.
- A region outage, or a customer contract, requires a second region.

## Open

- Owners: #10 Deployment & CI/CD is a proposal; confirm it.
- The budget for staging, and when the first apply happens.
- A domain, so the load balancer can serve HTTPS before real customers.
- The tenant workflow engine (ADR 0025 leaves Temporal or Argo Workflows open); it is not deployed.
- The weekly training CronJob is suspended until the training image is in Artifact Registry and a data release is in the snapshots bucket; registering runs in MLflow from the cluster, repo model fits and the label and drift triggers are not built.
- How the scorer computes live features the same way as training.
