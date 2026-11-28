# terraform

The GCP platform Riffle runs on: everything outside Kubernetes in the production architecture
(`site/public/architecture/`). Project `riffle-infra-platform`, region `us-east4`. What runs
inside the cluster is deployed by Argo CD from [`infra/k8s`](../k8s), not from here; Terraform
only installs Argo CD and the namespaces and settings it needs.

For now the code is **written and validated only**; nothing has been applied.

## Layout

```
infra/terraform/
├── modules/                 one folder per kind of resource, reusable, no provider config
│   ├── project-services/    Google APIs
│   ├── network/             VPC, subnet, private services access, Cloud NAT
│   ├── gke-autopilot/       the Kubernetes cluster and its node service account
│   ├── cloud-sql/           Postgres: instance, databases, IAM logins
│   ├── memorystore/         Redis
│   ├── storage-buckets/     Cloud Storage buckets and their lifecycle rules
│   ├── kms/                 the tenant key-encryption key
│   ├── secrets/             Secret Manager entries
│   ├── pubsub/              topics and subscriptions from infra/pubsub/topics.json
│   ├── service-accounts/    one Google account per service, Workload Identity, permissions
│   ├── artifact-registry/   the container registry
│   ├── github-oidc/         GitHub Actions access without keys
│   ├── edge/                load balancer IP and Cloud Armor
│   ├── monitoring/          uptime check and alert policies
│   └── team-access/         what people on the team may do (laptop access)
└── envs/
    ├── staging/             the cloud resources: one numbered file per phase, each calls one module
    │   ├── versions.tf      Terraform and provider versions, GCS backend, provider
    │   ├── variables.tf     every input, with defaults (address plan included)
    │   ├── locals.tf        names, labels, the list of APIs
    │   ├── 01-project-services.tf
    │   ├── 02-network.tf
    │   ├── 03-cluster.tf
    │   ├── 04-database.tf
    │   ├── 05-redis.tf
    │   ├── 06-storage.tf
    │   ├── 07-keys-and-secrets.tf
    │   ├── 08-pubsub.tf
    │   ├── 09-identity.tf   what each service may touch
    │   ├── 10-ci-access.tf
    │   ├── 11-edge.tf
    │   ├── 12-monitoring.tf
    │   ├── 13-team-access.tf
    │   └── outputs.tf
    └── staging-bootstrap/   inside the cluster, once it exists: namespaces, settings, Argo CD
        ├── namespaces.tf    the Riffle namespaces and the riffle-platform-settings ConfigMap
        ├── main.tf          Argo CD and the root Application (infra/k8s/apps/staging)
        └── values/argocd.yaml
```

Rules that keep a problem easy to find:

- **A module does one thing** and has the same files: `main.tf` (resources), `variables.tf`
  (inputs, with validation), `outputs.tf`, `versions.tf` (provider requirements only) and a
  `README.md`. A module never configures a provider or a backend.
- **An environment root only wires modules together.** Each `NN-<phase>.tf` file calls one
  module, so an error in `module.network...` is in `02-network.tf` and `modules/network/`.
- **Names come from `local.name`** (`riffle-staging`), so every resource in the console can be
  traced back to its environment; every resource also gets the labels in `locals.tf`.
- **No secret values typed into Terraform.** Secrets set by hand are created empty and filled
  with `gcloud`; the ones Terraform sets (generated or read from Google) are in its state, which
  lives in a private bucket.
- **Inputs are validated** in `variables.tf`, so a wrong value fails at `plan` with a clear
  message, not halfway through an apply.

## Check it (no GCP access needed)

```bash
cd infra/terraform
terraform fmt -check -recursive
for root in envs/staging envs/staging-bootstrap; do
  (cd $root && terraform init -backend=false -input=false && terraform validate)
done
```

The CI job `terraform` runs exactly this, with Terraform 1.9.

## Applying (later, not now)

Two roots, in this order, because the second talks to the cluster the first creates:

1. Install `gcloud`, sign in, and make sure project `riffle-infra-platform` exists with billing.
2. Create the state bucket once (Terraform cannot create the bucket that holds its own state):
   ```bash
   gcloud storage buckets create gs://riffle-infra-platform-tfstate --project riffle-infra-platform \
     --location us-east4 --uniform-bucket-level-access --public-access-prevention
   gcloud storage buckets update gs://riffle-infra-platform-tfstate --versioning
   ```
3. `envs/staging`: copy `backend.hcl.example` to `backend.hcl`, `terraform init
   -backend-config=backend.hcl`, `terraform plan`, `terraform apply`.
4. Fill the secrets set by hand (output `secrets_to_fill_by_hand`) and paste the webhook secret
   into the GitHub App.
5. `envs/staging-bootstrap`: the same `init`, `plan`, `apply`. Argo CD then deploys
   `infra/k8s/apps/staging` by itself.

## Access and credentials

Nothing is in KMS but encryption keys (the tenant key-encryption key, which never leaves it).
Secrets are in **Secret Manager**; most logins have **no password at all** and use a Google
identity instead.

| Credential | Where it is | Password? |
| --- | --- | --- |
| Postgres, services | Nowhere: each service logs in as its Google account through the Cloud SQL Proxy | No |
| Postgres, people | Nowhere: your Google account, listed in `db_people`, role `riffle_readonly`, through the `db-proxy` pod (the database has no public IP) | No |
| Postgres built-in `postgres` user | Never given a password, so it cannot log in. Do not set one in the console | — |
| Redis AUTH, webhook secret, dashboard session key, Grafana admin | Secret Manager, generated by Terraform (so also in its state) | Yes |
| GitHub App private key and client secret | Secret Manager, added by hand | Yes |
| Prometheus | Google Managed Prometheus: Google login and `monitoring.viewer` | No |
| Argo CD admin | Kubernetes Secret `argocd-initial-admin-secret`; change the password, then delete the Secret | Yes |
| MLflow | No login; only the pods the network rules allow (trainer, retrain, scorer) and `kubectl port-forward` reach it | — |
| GitHub Actions | A one-hour token per run (Workload Identity Federation) | No key |
| Terraform state | `gs://riffle-infra-platform-tfstate`, readable by `team_members` only | — |

Pods never read Secret Manager directly: External Secrets copies each secret, as the service's
own Google account, into the Kubernetes Secret `<service>-secrets` of that service only.

### From your laptop

Add yourself to `team_members` (and to `db_people` for the database) in `terraform.tfvars`;
the `team-access` module grants the roles. Then, once:

```bash
gcloud auth login
gcloud auth application-default login
gcloud config set project riffle-infra-platform
gcloud container clusters get-credentials riffle-staging --region us-east4 --dns-endpoint
```

| To | Run |
| --- | --- |
| Read a secret | `gcloud secrets versions access latest --secret=riffle-staging-<name>` (`gcloud secrets list` for names) |
| Every password in one local file | `infra/fetch-credentials.sh` writes `infra/credentials.local.md` (gitignored, mode 600); never commit or share it |
| Postgres | No public IP: start the proxy pod and forward it, `kubectl apply -f infra/k8s/tools/db-proxy.yaml` then `kubectl -n riffle-workers port-forward pod/db-proxy 5432:5432`; log in as yourself with `PGPASSWORD="$(gcloud sql generate-login-token)" psql "host=127.0.0.1 port=5432 dbname=riffle user=<your email> sslmode=disable"`, then `SET app.tenant_id = '<installation id>';`. Delete the pod when done (`kubectl delete -f infra/k8s/tools/db-proxy.yaml`) |
| Grafana | `kubectl -n observability port-forward svc/grafana 3000:80`, user `admin`, password from secret `riffle-staging-grafana-admin` |
| Argo CD | `kubectl -n argocd port-forward svc/argocd-server 8080:443`, user `admin`, password in `argocd-initial-admin-secret` |
| MLflow | `kubectl -n riffle-ml port-forward svc/mlflow 5000:5000` |
| Metrics | Grafana's Managed Prometheus data source, or Cloud Console, Metrics Explorer |
| Redis | Only from inside the cluster (private IP): a short-lived pod running `redis-cli --tls` with the AUTH secret |
| Terraform outputs | `terraform output` in `envs/staging` |

## Phases

| # | Phase | Where | Diagram box | Status |
| --- | --- | --- | --- | --- |
| 1 | Google APIs | `project-services`, `01-project-services.tf` | — | done |
| 2 | Network, private services access, Cloud NAT + egress IP | `network`, `02-network.tf` | NAT gateway | done |
| 3 | GKE Autopilot, regional, private nodes, Workload Identity | `gke-autopilot`, `03-cluster.tf` | Kubernetes cluster | done |
| 4 | Cloud SQL Postgres 17 (databases `riffle` and `mlflow`, private IP, IAM logins only, backups) | `cloud-sql`, `04-database.tf` | Postgres, Model registry | done |
| 5 | Memorystore Redis 7.2 (private, AUTH and TLS, no persistence) | `memorystore`, `05-redis.tf` | Redis | done |
| 6 | Cloud Storage buckets (score log, models, snapshots, logs, traces), private, with lifecycle rules | `storage-buckets`, `06-storage.tf` | Object storage, Score log | done |
| 7 | Cloud KMS key-encryption key (ADR 0025, rotated every 90 days) and Secret Manager entries | `kms`, `secrets`, `07-keys-and-secrets.tf` | Key vault | done |
| 8 | Pub/Sub from `infra/pubsub/topics.json` (5 topics), dead-letter topics, hold subscriptions | `pubsub`, `08-pubsub.tf` | Message queue | done |
| 9 | One service account per service (18), Workload Identity, least privilege; database logins | `service-accounts`, `09-identity.tf` | Cluster platform (pod identity) | done |
| 10 | Artifact Registry, and GitHub Actions access without keys (images from any branch, Terraform from main) | `artifact-registry`, `github-oidc`, `10-ci-access.tf` | CI/CD, Container registry | done |
| 11 | Edge: global static IP, Cloud Armor (GitHub-only `/webhook`, OWASP rules in preview, rate limit) | `edge`, `11-edge.tf` | Load balancer, WAF | done |
| 12 | Namespaces and platform settings, Argo CD; then from `infra/k8s`: policies, service accounts, External Secrets, KEDA, Kueue, Gateway, the database migration Job | `envs/staging-bootstrap`, `infra/k8s` | Cluster platform | done |
| 13 | Uptime check and alerts in Cloud Monitoring; Loki, Tempo, Alloy and Grafana from `infra/k8s` | `monitoring`, `12-monitoring.tf`, `infra/k8s` | Observability | done |
