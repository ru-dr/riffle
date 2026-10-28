# 0018: Terraform for the team store VM

- **Date:** 2026-10-27
- **Status:** Proposed
- **Owners:** #10 Deployment & CI/CD, with #5 Data ingestion

## Context

The team store VM was made by hand with `infra/docker/riffle-data/gcp/up.sh` (ADR 0015) and has run only the store. ADR 0017 adds Loki, Grafana and Airflow beside it, and a second script would make three places that describe the same machine. `infra/terraform` has been an empty plan. Datasets must live in a bucket, the VM must not hold a key file, and Airflow and Grafana must not be open to the internet.

## Decision

- One Terraform root module, `infra/terraform/store-vm`, builds the bucket, the VM's service account, a static address, the firewall rules and the VM.
- The VM's startup script installs Docker, checks out this repo at `repo_ref`, writes the `.env` files once (generated secrets stay across reboots) and starts `infra/docker/riffle-data` (with the `public` and, by default, `logs` profiles) and `infra/docker/airflow`. The Compose files remain the one description of the services.
- Only ports 80 and 443 are open to the internet. SSH is allowed only from IAP's range. Grafana and Airflow listen on the VM's loopback and are read through `gcloud compute ssh --tunnel-through-iap -L`.
- State is in a GCS bucket named at `terraform init`. Applying is done from CI or by the project owner, not by hand from a laptop.
- CI runs `terraform fmt -check` and `terraform validate` on the module (a separate `terraform` job).
- `gcp/up.sh` stays as the manual route. The existing VM is taken over with `terraform import` (module README).

## Options not taken

| Option | Why not |
| --- | --- |
| Keep growing `up.sh` | Not repeatable, no plan before a change, no record of what exists |
| Ship the code to the VM as a tarball from the laptop | Needs the laptop; a checkout of a ref is the same on every apply |
| One Terraform file per service (Airflow VM, store VM) | ADR 0013 and 0015 already put both on one VM; two VMs double the cost and Airflow could not read the store's disk |
| Secrets in Terraform variables | They would sit in state; generating them on the VM keeps them off the state bucket |
| GKE now | Mining stays on volunteers' PCs; Kubernetes is for the later services |

## Consequences

- New: `infra/terraform/store-vm`, a `terraform` CI job, `.gitignore` rules for `*.tfvars`.
- The first apply changes the existing VM's machine type (a stop and start) and startup script after an import; the plan must be read first.
- Airflow passwords and Grafana's password exist only on the VM's disk (`/opt/riffle`).
- No invariant is touched. Nothing leaves the installation except what PCs already send to the store.

## Revisit if

- The VM's memory cannot hold the store, Loki, Grafana, Airflow and Postgres together under load.
- Mining moves off volunteers' PCs.
- Another team needs its own store.

## Open

- Whether the repo stays public (the VM then needs no token) or `repo_token_secret` is used.
- Where the Terraform state bucket lives and who runs `apply`.
- The right machine size; `e2-standard-4` is a guess that has not been measured; Airflow's task processes are the largest user (capped by `AIRFLOW_PARALLELISM`).
