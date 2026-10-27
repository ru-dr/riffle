# store-vm

One GCP VM for the team: the store that the miners upload to ([ADR 0015](../../../docs/adr/0015-central-store-for-mined-data.md)),
Loki and Grafana for run traces, and Airflow with the `riffle_miner` DAG ([ADR 0017](../../../docs/adr/0017-miner-dag-loki-and-dvc.md)).
Everything runs from the Compose files in [`infra/docker`](../../docker); Terraform makes what they need around them
([ADR 0018](../../../docs/adr/0018-terraform-for-the-store-vm.md)).

| Resource | Why |
| --- | --- |
| Bucket (`bucket`) | Datasets and snapshot tables live only here; also the DVC remote. `prevent_destroy` is on |
| Service account | The VM's own identity: `objectAdmin` on that bucket, nothing else. No key file |
| Static address, firewall | Ports 80 and 443 open to the internet (pages and certificate). Port 22 only from IAP's range. Grafana and Airflow are not open at all |
| VM | Debian 12, shielded, OS Login. Its startup script installs Docker, checks out the repo at `repo_ref`, writes the `.env` files once and starts the stacks |

## Use it

```bash
cd infra/terraform/store-vm
cp terraform.tfvars.example terraform.tfvars          # project and bucket; git-ignored
terraform init -backend-config="bucket=YOUR_STATE_BUCKET" -backend-config="prefix=store-vm"
terraform plan
```

State lives in a GCS bucket you make once by hand (`gcloud storage buckets create gs://YOUR_STATE_BUCKET --uniform-bucket-level-access`, with versioning on).
Applying is done from CI or by the person who owns the project, not by hand from a laptop (`../README.md`). Nothing here has been applied yet.

After the first apply the VM needs 5 to 10 minutes to build the images. Follow it with
`gcloud compute ssh riffle-data --tunnel-through-iap --command 'tail -f /var/log/riffle-startup.log'`.

```bash
terraform output tunnel               # then open http://localhost:3000 (Grafana) and http://localhost:8080 (Airflow)
terraform output airflow_passwords    # the command that prints the Airflow logins from the VM
```

Then, on the VM, as in the store's README: `sudo docker compose exec riffle-data python manage.py add-reader lead`, and issue the PCs'
join codes from the admin page. PCs use `terraform output sync_url`. Grafana's login is `admin` with `GF_SECURITY_ADMIN_PASSWORD` from
`/opt/riffle/infra/.env`. Loki and Grafana are the project's logging stack (`infra/compose.logging.yaml`); Alloy is not started on the VM.

## Things to know

- **Pin `repo_ref`.** The VM fetches it and rebuilds as root on every boot, so `main` means "whatever was merged last". Use a tag or a commit
  for anything you rely on.
- **Port 22.** The IAP rule only adds access. A project's `default` network usually also has `default-allow-ssh` open to the internet; delete
  it (`gcloud compute firewall-rules delete default-allow-ssh`) once IAP works for you.
- **The VM's disk is not resized from here** (`type` and `size` are ignored, so importing the VM made by `up.sh` cannot replace it). Resize with
  `gcloud compute disks resize`. The VM has `prevent_destroy`: a plan that would replace it fails instead.
- **Airflow's packages** (`dvc[gs]`, pandas) are not pinned to Airflow's constraint file; a rebuild can move them. Check `pip check` in the image
  after one.
- Set `RIFFLE_DVC_ALLOWED_REMOTES` (comma-separated `gs://` prefixes) in the Airflow `.env` to limit where `publish` may push.

## Change the running code

Set `repo_ref` and apply, then on the VM: `sudo google_metadata_script_runner startup`. It is safe to run again: Docker is skipped, the
repo moves to the ref, `.env` files and their secrets are kept, and the stacks are rebuilt.

## Take over the VM made with `gcp/up.sh`

The first VM was made by [`riffle-data/gcp/up.sh`](../../docker/riffle-data/gcp/up.sh). To manage it from here, import what exists
instead of creating it again (names are the defaults):

```bash
P=YOUR_PROJECT; Z=us-central1-a; R=us-central1
terraform import google_compute_address.ip projects/$P/regions/$R/addresses/riffle-data-ip
terraform import google_compute_firewall.web projects/$P/global/firewalls/riffle-data-web
terraform import google_compute_instance.vm projects/$P/zones/$Z/instances/riffle-data
terraform import google_service_account.vm projects/$P/serviceAccounts/riffle-data@$P.iam.gserviceaccount.com
terraform import google_storage_bucket.data $P/YOUR_BUCKET
terraform plan        # read it: the machine type changes (a stop and start), the startup script changes, the IAP rule is new
```

Before the first boot with the new script, stop the old stack on the VM so the two checkouts do not fight over the same Compose project
(data volumes are kept; never pass `-v`): `cd /opt/riffle-data && sudo docker compose --profile public down`.

## Inputs

See [`variables.tf`](variables.tf). Only `project` and `bucket` are required. `enable_logs` and `enable_airflow` turn the extra stacks off
(e2-small is enough for the store alone); `repo_token_secret` names a Secret Manager secret for a private repo.
