# infra

How Riffle runs, per environment.

| Directory | Responsibility |
| --- | --- |
| [`docker`](docker) | Service images and the local Compose stacks (today: Airflow, the riffle-data team store) |
| [`k8s`](k8s) | Manifests for staging and prod |
| [`terraform`](terraform) | The team store VM (`store-vm`: store, Loki + Grafana, Airflow, bucket, IAM); later Postgres, Redis, Pub/Sub, GKE |

| Environment | Where | Notes |
| --- | --- | --- |
| `local` | Docker Compose | Emulated Pub/Sub, no GPU, stub explainer by default |
| `staging` | GKE, small pool | Test-org GitHub App, real Pub/Sub, one shared GPU node |
| `prod` | GKE | One shared GPU node for the explainer; training is CPU only |
