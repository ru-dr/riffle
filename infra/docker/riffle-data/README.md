# riffle-data

The team store for the repo miner's output (ADR 0015). Each PC's miner sends its
logs, health files, dataset tables and a `run.json` here after each repo, on a
Stop and at the end. This service stores them, shows progress per PC, searches
the logs, and keeps the parquet tables in a bucket for BigQuery.

| Part | What it does |
| --- | --- |
| `server/app.py` | `PUT /v1/files/<key>` for PCs; pages and JSON for the lead. Standard library only |
| `server/store.py` | Small files (logs, health, `run.json`) stay on the VM disk; with `RIFFLE_BUCKET` every file is also in GCS and big ones live only there |
| `server/index.py` | SQLite index of files and runs. A cache: `manage.py reindex` rebuilds it |
| `server/tokens.py`, `manage.py` | One write-only token per PC, read tokens for the UI. Only hashes are stored |
| `server/bq_load.py` | Loads the PCs' parquet tables from the bucket into BigQuery |

## What the pages show

`/` lists each PC: state (mining, done, stopped, silent), repos done of total, the
repo it is mining now, PRs mined, and PRs left. **PRs left covers repos already
started.** A repo that has not started has no PR selection yet (the miner picks
PRs when it begins a repo), so it counts under "repos waiting" instead. A grand
total of PRs is unknown until every repo has started. `/run` gives one PC's repos
with rows, selected PRs, left and health flags, and its files. `/search` greps the
run logs (traces stay in the bucket, not searched).

## Run it on a laptop (no GCP)

```bash
cd infra/docker/riffle-data
cp .env.example .env
docker compose up -d --build
docker compose exec riffle-data python manage.py add-reader lead   # UI password, shown once
docker compose exec riffle-data python manage.py add-pc pc1        # that PC's RIFFLE_SYNC_TOKEN
```

Open http://127.0.0.1:8470 (any username, the reader token as password). On a PC,
set `RIFFLE_SYNC_URL` and `RIFFLE_SYNC_TOKEN` in the miner's `.env`.

## Run it on GCP

This has been run only on a laptop, never on GCP. You need a Google Cloud
organisation or account, a project with billing, and `gcloud` signed in to it.
Replace the names.

```bash
gcloud config set project YOUR_PROJECT
gcloud services enable compute.googleapis.com storage.googleapis.com bigquery.googleapis.com

# bucket for the tables, a service account for the VM, and BigQuery access
gcloud storage buckets create gs://YOUR_BUCKET --location=US --uniform-bucket-level-access
gcloud iam service-accounts create riffle-data
SA=riffle-data@YOUR_PROJECT.iam.gserviceaccount.com
gcloud storage buckets add-iam-policy-binding gs://YOUR_BUCKET --member=serviceAccount:$SA --role=roles/storage.objectAdmin
gcloud projects add-iam-policy-binding YOUR_PROJECT --member=serviceAccount:$SA --role=roles/bigquery.jobUser
gcloud projects add-iam-policy-binding YOUR_PROJECT --member=serviceAccount:$SA --role=roles/bigquery.dataEditor

# one VM (Debian 12, 2 vCPU / 4 GB, 50 GB disk) with that service account, no key file
gcloud compute instances create riffle-data --machine-type=e2-medium --boot-disk-size=50GB \
  --image-family=debian-12 --image-project=debian-cloud --service-account=$SA --scopes=cloud-platform
```

On the VM: install Docker, clone the repo, `cp .env.example .env`, set
`RIFFLE_BUCKET`, `GOOGLE_CLOUD_PROJECT` and `RIFFLE_BIND_ADDR=0.0.0.0` only if a
firewall rule or tunnel limits who can connect, then `docker compose up -d --build`
and issue tokens as above.

**Reaching it.** The service speaks plain HTTP, so tokens would cross the network in clear.
Do not open port 8470 to the internet. Use one of: an IAP TCP tunnel
(`gcloud compute start-iap-tunnel riffle-data 8470 --local-port=8470`), Tailscale on the VM
and the PCs, or a TLS proxy (Caddy) with a firewall rule for your team's addresses. For
IAP and PCs, point `RIFFLE_SYNC_URL` at the tunnel or Tailscale address.

## Load into BigQuery

```bash
docker compose exec riffle-data python bq_load.py --dataset riffle_mined --version 6.6.0 --split SPLIT_ID --dry-run
docker compose exec riffle-data python bq_load.py --dataset riffle_mined --version 6.6.0 --split SPLIT_ID --only-finished
```

It writes `mined_dataset`, `mined_snapshot` and `mined_commit_features` (the contract
names) from every PC's parquet files, replacing each table, so rerun it as PCs finish.
The three tables stay separate. They have different grains and are joined in SQL or by
`pipelines/training/frames.py`. A PC without pandas and pyarrow writes JSONL, which is
reported and skipped.

## Tests

```bash
python -m pytest infra/docker/riffle-data/tests -q
```

They run the server on localhost with a local store, including the miner's own
`sync.py` against it, and check which files go to which BigQuery table. The GCS
and BigQuery calls themselves are not covered, because they need a real project.
