# 0013: Airflow on one Compose-managed VM for now

- **Date:** 2026-10-20
- **Status:** Proposed
- **Owners:** #10 Deployment & CI/CD, with #5 Data ingestion

## Context

`pipelines/dags` needs a scheduler for nightly ingestion, retraining and drift
measurement. `infra/README.md` says staging and prod run on GKE, but no cluster
manifests exist yet, and the DAGs are small. The team needs a scheduler that a
person can run locally and that can also run, unattended, on one cloud machine.
Airflow's Celery executor needs extra services (workers and Redis) that one
machine does not need.

## Decision

- Run Airflow 3.3 with `LocalExecutor` and a Postgres metadata database from one
  Compose file, `infra/docker/airflow/compose.yaml`. The same file runs on a
  laptop and on a single GCP VM (Debian or Ubuntu, 4 GB of memory or more); the
  difference is `.env`.
- Heavy work does not run in these containers. A DAG starts a Kubernetes Job or
  calls the code in `pipelines/ingestion` and `pipelines/training`, which stays
  runnable without Airflow.
- The image is built locally and never pulled (`riffle-airflow:<version>-local`,
  `pull_policy: never`), so nobody else's image can run with our secrets.
- Secrets live in a `.env` file on the machine, which is gitignored. The UI
  listens on `127.0.0.1` by default and is reached through an IAP tunnel.
  Logins are per person through the simple auth manager. The VM uses an attached
  service account for Google APIs, never key files.
- State is two Docker volumes (`postgres-data`, `airflow-logs`).

## Options not taken

| Option | Why not |
| --- | --- |
| Run Airflow on GKE now | `infra/k8s` and `infra/terraform` hold only READMEs, so there is no cluster to run it on yet |
| CeleryExecutor with Redis | Needs workers and Redis; one machine does not (`infra/docker/airflow/README.md`) |

Other options the team may have weighed (a managed Airflow, no scheduler) are
not recorded; see Open.

## Consequences

- One machine is a single point of failure for scheduling. The product's request
  path does not depend on it (`pipelines/README.md`: nothing there is on the
  request path), so an outage delays training and ingestion only.
- Passwords sit in plain text in `.env`, and removing a person means editing it
  and replacing the API secrets (`infra/docker/airflow/README.md`).
- No TLS of its own: the UI must stay on localhost or behind a firewall rule or
  a TLS proxy.
- Riffle's invariants are not touched: the LLM is not on the scoring path,
  nothing skips review, Riffle does not block merges, nothing leaves the
  installation by default, and scoring stays point-in-time.

## Revisit if

- GKE staging exists and the DAG count or run time outgrows one machine.
- More than a handful of people need logins, or SSO is required.
- The metadata database needs backups or failover that a Docker volume cannot give.

## Open

- Which other options were considered, and why they were dropped.
- Backups: who snapshots the `postgres-data` volume, how often, and where to.
- Whether the metadata database moves to Cloud SQL, and when.
- Secret Manager in place of `.env`.
- Owners: the workstream above is a proposal; confirm it.
