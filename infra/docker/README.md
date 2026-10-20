# docker

[`airflow/`](airflow) is the one thing here today: the Docker Compose stack that runs
Airflow locally and on a single VM ([ADR 0013](../../docs/adr/0013-airflow-on-a-compose-vm.md)).

Planned: one Dockerfile per service, plus the Compose stack behind `make up`:
Postgres, Redis, a Pub/Sub emulator, and all four services.

Local defaults to the stub explainer — no GPU required to run the product.
