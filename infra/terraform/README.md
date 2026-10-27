# terraform

Managed dependencies: Postgres (features, tenants, outcomes), Redis
(idempotency keys, inference cache), Pub/Sub topics and subscriptions, the DVC
bucket, GKE pools, and IAM.

State is remote. Day to day, applies run from CI. The first apply of each root
is done by hand from a laptop, because there is no CI identity until Workload
Identity Federation exists; after that, change it through a pull request.

| Directory | What it holds |
| --- | --- |
| [`foundation`](foundation) | Ingestion base on GCP: raw bucket, BigQuery datasets, Artifact Registry, job service accounts, budget alert |
| [`store-vm`](store-vm) | The team store VM on GCP: bucket, service account, address, firewall, and the VM that runs the store, Loki + Grafana and Airflow from `infra/docker` and `infra/compose.logging.yaml` (ADR 0018) |

## foundation

Default region `us-east4` (Northern Virginia). Project-specific values (project
id, billing account, bucket names) are never committed: they live in
`terraform.tfvars` and `backend.hcl`, both gitignored. Copy the matching
`*.example` file for each and fill it in.

One-off, before the first `terraform init`: create the state bucket, which
Terraform cannot create for itself. Its location does not need to match the
resources it tracks.

```bash
gcloud storage buckets create gs://<project-id>-tfstate \
  --project <project-id> --location <region> \
  --uniform-bucket-level-access --public-access-prevention
gcloud storage buckets update gs://<project-id>-tfstate --versioning
```

Then, from `infra/terraform/foundation`:

```bash
cp terraform.tfvars.example terraform.tfvars   # project id, billing account, raw bucket name
cp backend.hcl.example backend.hcl             # state bucket name
terraform init -backend-config=backend.hcl
terraform plan
terraform apply   # only after reading the plan
```

Needs Application Default Credentials (`gcloud auth application-default login`).
Never commit `terraform.tfvars`, `backend.hcl`, a `tfplan` file or anything
under `.terraform/`; all are gitignored.

The raw bucket has versioning on and a retention period (default 30 days,
unlocked), so no object version can be permanently destroyed before it expires.
That does not stop an authorised user replacing or deleting the live object:
the old version is kept and can be restored. Append-only is therefore enforced
by the job accounts having no delete role and by writing with
`if_generation_match=0`. Lock the policy only once the object layout is settled:
a locked policy cannot be shortened or removed.
