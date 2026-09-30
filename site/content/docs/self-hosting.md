Riffle is self-hosted: it runs in your infrastructure, and code, diffs and
author identities never leave it. Outcome sharing is off by default.

> **Planned.** Deployment manifests and images land with the first runnable
> services. The environments below are the intended shape.

## Environments

| Environment | Where | Notes |
| --- | --- | --- |
| `local` | Docker Compose | Emulated Pub/Sub, no GPU, stub explainer by default |
| `staging` | GKE, small pool | A real GitHub App on a test organisation, real Pub/Sub, one shared GPU node |
| `prod` | GKE | One shared GPU node for the explainer; per-tenant training is CPU only |

## What you run

| Layer | Components |
| --- | --- |
| Services | `intake`, `scorer`, `explainer`, `app` |
| State | Postgres, Redis |
| Messaging | Pub/Sub |
| Training | Airflow, MLflow, Kubernetes Jobs |
| Autoscaling | KEDA, driven by `riffle_queue_depth` |

## Data you keep

| Setting | Default | What it controls |
| --- | --- | --- |
| `organization.outcome_sharing.enabled` | `false` | Whether anonymised outcomes improve the global model |
| `organization.data_retention.store_diffs` | `false` | Whether full diffs are stored after features are computed |
| `organization.data_retention.retention_days` | `180` | How long stored data is kept |

Credentials appear in configuration only as secret references, never as
values.

## Running without an LLM

The explainer is optional. With `llm.provider: none`, every explanation comes
from the deterministic template, and ranking is unaffected — the LLM is never
on the correctness path.
