# terraform

The GCP platform Riffle runs on: everything outside Kubernetes in the production architecture
(`site/public/architecture/`). Project `riffle-infra-platform`, region `us-east4`. What runs
inside the cluster is deployed by Argo CD from `infra/k8s`, not from here.

For now the code is **written and validated only**; nothing has been applied.

## Layout

```
infra/terraform/
├── modules/                 one folder per kind of resource, reusable, no provider config
│   ├── project-services/    Google APIs
│   ├── network/             VPC, subnet, private services access, Cloud NAT
│   ├── gke-autopilot/       the Kubernetes cluster and its node service account
│   ├── cloud-sql/           Postgres: instance, databases, IAM logins
│   └── memorystore/         Redis
└── envs/
    └── staging/             one root per environment: state, provider, variables
        ├── versions.tf      Terraform and provider versions, GCS backend, provider
        ├── variables.tf     every input, with defaults (address plan included)
        ├── locals.tf        names, labels, the list of APIs
        ├── 01-project-services.tf
        ├── 02-network.tf    one numbered file per phase: each calls one module
        ├── 03-cluster.tf
        ├── 04-database.tf
        ├── 05-redis.tf
        └── outputs.tf
```

Rules that keep a problem easy to find:

- **A module does one thing** and has the same four files: `main.tf` (resources),
  `variables.tf` (inputs, with validation), `outputs.tf`, `versions.tf` (provider
  requirements only). A module never configures a provider or a backend.
- **An environment root only wires modules together.** Each `NN-<phase>.tf` file calls one
  module, so an error in `module.network...` is in `02-network.tf` and `modules/network/`.
- **Names come from `local.name`** (`riffle-staging`), so every resource in the console can be
  traced back to its environment; every resource also gets the labels in `locals.tf`.
- **No secret values in Terraform.** Secret Manager entries are created empty and filled by
  hand.
- **Inputs are validated** in `variables.tf`, so a wrong value fails at `plan` with a clear
  message, not halfway through an apply.

## Check it (no GCP access needed)

```bash
cd infra/terraform
terraform fmt -check -recursive                  # formatting
cd envs/staging
terraform init -backend=false -input=false       # downloads providers only, no state
terraform validate
```

The CI job `terraform` runs exactly this, with Terraform 1.9.

## Before the first apply (later, not now)

1. Install `gcloud`, sign in, and make sure project `riffle-infra-platform` exists with billing.
2. Create the state bucket once (Terraform cannot create the bucket that holds its own state):
   ```bash
   gcloud storage buckets create gs://riffle-infra-platform-tfstate --project riffle-infra-platform \
     --location us-east4 --uniform-bucket-level-access --public-access-prevention
   gcloud storage buckets update gs://riffle-infra-platform-tfstate --versioning
   ```
3. In `envs/staging`: copy `backend.hcl.example` to `backend.hcl`, then
   `terraform init -backend-config=backend.hcl` and `terraform plan`.

## Phases

| # | Phase | Module | Root file | Diagram box | Status |
| --- | --- | --- | --- | --- | --- |
| 1 | Google APIs | `project-services` | `01-project-services.tf` | — | done |
| 2 | Network, private services access, Cloud NAT + egress IP | `network` | `02-network.tf` | NAT gateway | done |
| 3 | GKE Autopilot, regional, private nodes, Workload Identity | `gke-autopilot` | `03-cluster.tf` | Kubernetes cluster | done |
| 4 | Cloud SQL Postgres 17 (databases `riffle` and `mlflow`, private IP, IAM logins only, backups) | `cloud-sql` | `04-database.tf` | Postgres, Model registry | done |
| 5 | Memorystore Redis 7.2 (private, AUTH and TLS, no persistence) | `memorystore` | `05-redis.tf` | Redis | done |
| 6 | Cloud Storage buckets (score log, models, snapshots, logs, traces) | `storage` | `06-storage.tf` | Object storage, Score log | to do |
| 7 | Cloud KMS key-encryption key (ADR 0025) and Secret Manager entries | `kms`, `secrets` | `07-keys-and-secrets.tf` | Key vault | to do |
| 8 | Pub/Sub from `infra/pubsub/topics.json`, dead-letter permissions | `pubsub` | `08-pubsub.tf` | Message queue | to do |
| 9 | One service account per service, Workload Identity, least privilege | `service-accounts` | `09-identity.tf` | Cluster platform (pod identity) | to do |
| 10 | GitHub Actions access without keys (Workload Identity Federation) | `github-oidc` | `10-ci-access.tf` | CI/CD | to do |
| 11 | Edge: global static IP, Cloud Armor (GitHub hook IPs, rate limits); HTTPS once there is a domain | `edge` | `11-edge.tf` | Load balancer, WAF | to do |
| 12 | Argo CD bootstrap, then `infra/k8s`: namespaces, NetworkPolicy, External Secrets, KEDA, Gateway, the database migration Job | — | own root | Cluster platform | to do |
| 13 | Observability base: Managed Prometheus, Loki and Tempo storage, uptime check | — | — | Observability | to do |
