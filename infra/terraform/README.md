# terraform

Managed dependencies: Postgres (features, tenants, outcomes), Redis
(idempotency keys, inference cache), Pub/Sub topics and subscriptions, the DVC
bucket, GKE pools, and IAM.

| Module | What it builds |
| --- | --- |
| [`store-vm`](store-vm) | The team store VM on GCP: bucket, service account, address, firewall, and the VM that runs the store, Loki + Grafana and Airflow from `infra/docker` (ADR 0018) |

State is remote. Nothing here is applied by hand from a laptop.
