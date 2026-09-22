# terraform

Managed dependencies: Postgres (features, tenants, outcomes), Redis
(idempotency keys, inference cache), Pub/Sub topics and subscriptions, the DVC
bucket, GKE pools, and IAM.

State is remote. Nothing here is applied by hand from a laptop.
