# Infra handover

- **Date:** 2026-11-28
- **Branch:** `infra/platform-services`
- **From:** #10 Deployment & CI/CD (infra)

The state of the production platform (ADR 0026, Kubernetes on GCP, project `riffle-infra-platform`,
region `us-east4`) and what is left, by who picks it up. Terraform is written and validated but has
**never been applied**: nothing exists in GCP yet.

## What is built

| Part | Where | What it does |
| --- | --- | --- |
| GCP platform | `infra/terraform` (`envs/staging`, `envs/staging-bootstrap`) | Network, GKE Autopilot (regional), Cloud SQL Postgres 17 (private IP only), Memorystore, buckets, KMS `tenant-kek`, Secret Manager, Pub/Sub from `infra/pubsub/topics.json`, one Google account per service, CI access, load balancer and Cloud Armor, alerts, team access |
| Cluster platform | `infra/k8s` (Argo CD app of apps from `main`) | Policies and quotas, service accounts, External Secrets, KEDA, Kueue, Gateway, database migrations, Loki, Tempo, Alloy, Grafana and dashboards, MLflow, weekly training (suspended), the services |
| intake | `services/intake` (Go) | GitHub webhooks: verify, dedupe, publish `pr-events` and `installation-events` |
| scorer | `services/scorer` (Python) | `pr-features` → setup G model from MLflow (`riffle-outcome-setup-g@champion`) → `score_results` → `score-results` and `score-log` (ADR 0027) |
| scorelog-writer | `services/scorelog-writer` (Python) | `score-log` → Parquet per tenant and day, encrypted with each tenant's key (ADR 0024, ADR 0028) |
| Database | `infra/db/migrations` | `tenants`, `repos`, `score_results`, `tenant_keys`; row-level security on every table; `riffle_readonly` for people |
| Contracts | `contracts/` | `pr_event`, `installation_event`, `pr_features`, `score_result`, `score_log`, the miner tables, config |
| Monitoring | `infra/terraform/modules/monitoring`, `infra/k8s/platform/dashboards` | Cloud Monitoring alerts (uptime, dead letters, backlog, intake, scorer, score log); Grafana "Riffle: live path" and "Riffle: logs" |
| Logging | `infra/k8s/apps/staging/12-alloy.yaml` | Every Riffle and platform pod's logs and the Kubernetes events to Loki (30 days), one `level` label for every format; a second copy in Cloud Logging. `infra/k8s/README.md`, Logs |

The live path, and what is missing from it:

```
GitHub ─► intake ──pr-events──► pr-miner ──pr-features──► scorer ──score-results──► app
         (built)               (NOT BUILT)               (built)  └──score-log──► scorelog-writer (built)
```

Until the pr-miner exists, the scorer is tested by publishing `fixtures/pr_features/pull_request.opened.json`
by hand (`services/scorer/README.md`).

## How to check everything

Every CI job (`.github/workflows/ci.yml`) passed on this branch on 2026-11-28, run locally in containers
with CI's versions:

| Job | Result |
| --- | --- |
| `build` (site, Bun 1.3.14) | Built |
| `contracts` | `REFERENCE.md` up to date; 666 passed |
| `pipelines` | 641 + 50 + 5 + 12 + 151 passed |
| `model-gates` | Passed (two "reported only" rows are informational) |
| `logging` | 25 passed |
| `intake` (Go 1.27.4, race detector) | All packages ok, `gofmt` and `go vet` clean |
| `scorer` (with Postgres 17) | 51 passed, 1 skipped (needs the trained model: `RIFFLE_MODEL_DIR`) |
| `scorelog-writer` (with Postgres 17) | 24 passed |
| `aidev` | 117 passed, 3 skipped (network) |
| `terraform` (1.9.8) | `fmt` clean, both roots valid |
| `images.yml` | intake, outcome-training, scorer, scorelog-writer images build |
| Workflows | `actionlint` clean |
| Kubernetes | All 19 Argo CD Applications and the Argo CD install render; 319 resources, 0 invalid (kubeconform, with the CRD catalog) |

The miner's tests need `git`, and two logging and aidev tests need `docker` and `git`; GitHub's runners
have both.

## Before the first apply (infra owner)

Steps in `infra/terraform/README.md`, Applying:

1. Agree the staging budget. Then create the state bucket and apply `envs/staging`, then `envs/staging-bootstrap`.
2. Add the hand-filled secrets (`terraform output secrets_to_fill_by_hand`): the GitHub App private key and client secret.
3. Set the three repository variables for `images.yml` (`GCP_WORKLOAD_IDENTITY_PROVIDER`, `GCP_CI_IMAGES_SERVICE_ACCOUNT`, `GCP_IMAGE_REGISTRY`).
4. Merge to `main`: Argo CD deploys only from `main`.
5. Put each pushed image's tag in `infra/k8s/apps/staging/20-intake.yaml`, `21-scorer.yaml` and `22-scorelog-writer.yaml` (`image.tag`, now `not-built-yet`), and in `infra/k8s/platform/training/kustomization.yaml` (`newTag`).
6. `infra/fetch-credentials.sh` writes every password to `infra/credentials.local.md` (gitignored). Never commit it.
7. Change the Argo CD admin password, then delete `argocd-initial-admin-secret`. This is left until testing is done.

## Pending, by who picks it up

### Infra (next owner)

| Item | Notes |
| --- | --- |
| Budget alert | No `google_billing_budget` in Terraform yet; needs a monthly amount |
| Tune scaling and size after testing | Requests, max pods, messages per pod, Cloud SQL tier, scale-to-zero for background services. Testing gives the numbers |
| Database connections | The scorer opens up to 16 per pod (`WORKERS`); 10 pods can exceed what a 1 vCPU instance allows. Cap per pod or add a pooler (PgBouncer) if testing shows it |
| Read replica, Redis cache for the dashboard | Only if testing shows the dashboard's reads slow the writes |
| Tenant workflow engine | Temporal or Argo Workflows is still open (ADR 0025). It creates tenants, which the scorer and writer need |
| Error tracking | Grouping exceptions across services; a tool is not chosen |
| Traces | Alloy and Tempo are ready (the chart sets `OTEL_EXPORTER_OTLP_ENDPOINT`); no service sends traces yet |
| Production environment | Only `envs/staging` exists. The prod settings are noted in the variables (`REGIONAL` Cloud SQL, `STANDARD_HA` Redis) |
| Re-wrapping tenant keys | When `tenant-kek` is rotated (ADR 0028, Open) |
| Cloud Logging copy | Kept on as the backup when the cluster is down; turning workload logs off there saves cost |
| Alerts and dashboard rows for new services | Add one as each service lands |

### GitHub App (app, intake)

| Item | Notes |
| --- | --- |
| `app-github` and `app-dashboard` | Read `score-results`; post check runs; the dashboard reads `score_results`. Routes and secrets exist (`infra/k8s/platform/gateway`, `secrets`) |
| `outcome-events` | Nobody publishes them yet (closed or merged PRs, pushes), so there are no new labels for retraining (`services/intake/README.md`, Known limits) |
| Tenant rows | The scorer and writer need a `tenants` row per installation (the tenant workflow, ADR 0025). Until then, add it by hand to test |

### Data ingestion (pr-miner)

| Item | Notes |
| --- | --- |
| Three miner changes | GitHub App login, a saved clone and history index, callable as a library (`services/pr-miner/README.md`) |
| The pr-miner service | `pr-events` → a `mined_dataset` row → `pr-features` (ADR 0027). Its identity, secret and topic exist |
| History store | `gs://<snapshots bucket>/history/tenant=<id>/repo=<owner>__<name>.parquet`: the backfill at install and closed PRs added later. Until then every repo scores as if it had no history. Not yet encrypted with the tenant's key |

### Explainer and LLM

| Item | Notes |
| --- | --- |
| Decide called directly or through a queue | Recommended: read `score-log` (it carries the inputs), so a score never waits. Needs an ADR |
| Per-PR SHAP reasons, plain-language text per input | LightGBM gives per-row contributions; 161 inputs need wording |
| Contract and storage | `explain.schema.json` has no file; `score_results` is append-only, so an explanation needs its own table or message |
| Optional LLM | Off by default (`org_config` `llm.provider: none`); redaction, `excluded_paths`, per-tenant limit, cache, timeout to the template. Hosted needs an API key secret; self-hosted needs GPUs |
| The service | Code, Dockerfile, CI job, image, Argo CD app, metrics, alerts |

### Testing

| Item | Notes |
| --- | --- |
| Real model | Upload with `register_model.py`, set the model version's `miner_version` tag, run the scorer's skipped test with `RIFFLE_MODEL_DIR` |
| End to end on GCP | Publish the fixture to `pr-features`; check `score_results`, the score log files and the dashboards |
| Real Pub/Sub | The services' Pub/Sub wiring is tested with fakes only |
| Load test | Gives the numbers for scaling, Cloud SQL size and cost (Infra above) |
| `db-proxy` login | Logging in as yourself with `gcloud sql generate-login-token` through `infra/k8s/tools/db-proxy.yaml` only works on GCP |
| Alloy in the cluster | The log rules were run in Alloy 1.22 on sample lines; check the labels on real pods |

### Training and models

| Item | Notes |
| --- | --- |
| Register runs from the cluster | `register_model.py` needs `mlflow` in the training image; the weekly CronJob is suspended until the image and a data release exist |
| Score log reader | ADR 0024's loader beside `pipelines/training/frames.py` |
| Repo-specific model | For premium; the scorer serves `new_repos` only (`layer_weight` 0) |

## Decisions still Proposed

These need their owners' agreement: ADR 0026 (GCP platform), 0027 (live PR rows from the repo miner),
0028 (tenant data keys in Postgres). ADR 0025 leaves the workflow engine, the grace period and key
caching open. ADR 0024 leaves the score log's retention open.

## Things to know

- **The database has no public IP.** People connect through `infra/k8s/tools/db-proxy.yaml` and
  `kubectl port-forward`, logging in as themselves (`infra/terraform/README.md`, From your laptop).
- **Grafana, Argo CD and MLflow have no public address.** Use `kubectl port-forward` (same section).
  Their data lives outside the cluster (buckets, Managed Prometheus, Cloud Monitoring, git), so a
  rebuilt cluster finds it again.
- **The Alloy chart runs its config through Helm's `tpl`.** Alloy's own templates are wrapped in Helm's
  raw-string markers; keep curly-brace pairs out of comments there.
- **The model's inputs are never stored in Postgres** (ADR 0024); they are in the score log only.
- **Rules that apply to every change:** ADR rules in `docs/adr/PROMPT.md`; contract changes need
  `REFERENCE.md`, a row in `docs/contracts.md` and a version bump; branch names `<prefix>/<kebab>`.
