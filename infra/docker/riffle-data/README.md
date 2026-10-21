# riffle-data

The team store for the repo miner's output (ADR 0015). Each PC's miner sends its
logs, health files, dataset tables and a `run.json` here after each repo, on a
Stop and at the end. This service stores them, shows progress per PC, searches
the logs, and keeps the parquet tables in a bucket, which DuckDB queries (BigQuery is optional).

| Part | What it does |
| --- | --- |
| `server/app.py` | `PUT /v1/files/<key>` for PCs; pages and JSON for the lead. Standard library only |
| `server/store.py` | Small files (logs, health, `run.json`) stay on the VM disk; with `RIFFLE_BUCKET` every file is also in GCS and big ones live only there |
| `server/index.py` | SQLite index of files and runs. A cache: `manage.py reindex` rebuilds it |
| `server/tokens.py`, `manage.py` | One write-only token per PC, read tokens for the UI. Only hashes are stored |
| `server/duck.py` | DuckDB views over the PCs' parquet tables, from the bucket or the store's disk |
| `server/bq_load.py` | Optional: loads the same tables into BigQuery |
| `server/tables.py` | Which uploaded file belongs to which mined table (shared by the two above) |

## What the pages show

The pages use the site's design (hairline frame, Geist, the same tokens as the miner web
view) and draw their charts in SVG with no library. Sign in with a read token (it is kept
in an HttpOnly cookie); scripts can send it as `Authorization: Bearer`.

| Page | What it shows |
| --- | --- |
| `/` | Per PC: state, repos done of total, the repo being mined, PRs mined and left, plus charts of PRs per PC and repo states |
| `/run` | One PC's run: PRs per repo, health flags, every file it sent |
| `/data` | The three mined tables over every PC's parquet files, with a version and split picker |
| `/view?key=…` | **Parquet** as charts (PRs per repo, rows per month, outcome labels, null share), a sortable and filterable table, and a column list where any column can be charted. **JSON** as a tree with a summary for health and run files. **JSONL** as a filterable table. **Traces** with seconds per stage, pace and warnings. **Logs** with line numbers, filtering and jump to end |
| `/search` | Grep over the run logs and traces of every PC |

**PRs left covers repos already started.** A repo that has not started has no PR selection
yet (the miner picks PRs when it begins a repo), so it counts under "waiting". A grand total
of PRs is unknown until every repo has started.

The viewer reads parquet with DuckDB, limited to the store's folders, and only uses column
names from the file's own schema. Big files that live only in the bucket are downloaded to
`<data>/cache` the first time they are opened.

## Tokens: who can do what

| Kind | Made with | Can |
| --- | --- | --- |
| **admin** (`ad_…`) | `manage.py add-admin NAME` | issue and revoke PC tokens; reads nothing, writes no files. Lives only in the lead's `.env` as `RIFFLE_ADMIN_TOKEN` |
| **PC** (`rw_…`) | `build_dataset.py … --shard-plan N --issue-tokens` (or `manage.py issue-split ID N`) | write under its own name, and only under its own split; optionally expires |
| **reader** (`rd_…`) | `manage.py add-reader NAME` | read the pages and the APIs |

The normal flow: the lead plans the split with `--shard-plan N --issue-tokens`, which calls
`POST /v1/admin/splits`, and sends each PC its join code privately. A PC runs `python join.py CODE`
(see the miner's README). Planning the same split again rotates its tokens; `POST /v1/admin/revoke`
with a `split_id` or a `name` (or `manage.py revoke-split ID`) cuts access at once, and
`GET /v1/admin/pcs` shows each token's split, expiry and last upload. Only hashes are stored.

## Run it on a laptop (no GCP)

```bash
cd infra/docker/riffle-data
cp .env.example .env
docker compose up -d --build
docker compose exec riffle-data python manage.py add-reader lead   # UI password, shown once
docker compose exec riffle-data python manage.py add-admin lead    # the lead's RIFFLE_ADMIN_TOKEN, shown once
# one PC by hand, without a split (rarely needed; --issue-tokens is the normal way):
docker compose exec riffle-data python manage.py add-pc pc1
```

Open http://127.0.0.1:8470 and paste the reader token. The lead puts `RIFFLE_SYNC_URL` and
`RIFFLE_ADMIN_TOKEN` in the miner's `.env`, then plans the split with `--issue-tokens`.

## Run it on GCP

This has been run only on a laptop, never on GCP. You need a Google Cloud
organisation or account, a project with billing, and `gcloud` signed in to it.
Replace the names.

```bash
gcloud config set project YOUR_PROJECT
gcloud services enable compute.googleapis.com storage.googleapis.com

# bucket for the tables and a service account for the VM
gcloud storage buckets create gs://YOUR_BUCKET --location=US --uniform-bucket-level-access
gcloud iam service-accounts create riffle-data
SA=riffle-data@YOUR_PROJECT.iam.gserviceaccount.com
gcloud storage buckets add-iam-policy-binding gs://YOUR_BUCKET --member=serviceAccount:$SA --role=roles/storage.objectAdmin

# one VM (Debian 12, 2 vCPU / 4 GB, 50 GB disk) with that service account, no key file
gcloud compute instances create riffle-data --machine-type=e2-medium --boot-disk-size=50GB \
  --image-family=debian-12 --image-project=debian-cloud --service-account=$SA --scopes=cloud-platform
```

`gcp/up.sh` does all of this after the bucket and service account exist: it reserves an address, opens ports 80 and 443, creates the VM, copies this folder to it and starts the `public` profile on `https://<address>.sslip.io`. It has not been run yet. By hand, on the VM: install Docker, clone the repo, `cp .env.example .env`, set
`RIFFLE_BUCKET`, `GOOGLE_CLOUD_PROJECT` and `RIFFLE_BIND_ADDR=0.0.0.0` only if a
firewall rule or tunnel limits who can connect, then `docker compose up -d --build`
and issue tokens as above.

**Public address (HTTPS).** The service speaks plain HTTP, so it is never published directly.
For a public address use the `public` profile, which adds Caddy with an automatic Let's
Encrypt certificate:

```bash
# .env: a name that points at the VM (for a bare IP, 34-1-2-3.sslip.io works)
RIFFLE_DOMAIN=34-1-2-3.sslip.io
RIFFLE_TRUST_PROXY=1
RIFFLE_COOKIE_SECURE=1
RIFFLE_BIND_ADDR=127.0.0.1      # only Caddy is reachable from outside
docker compose --profile public up -d --build
```

Open ports 80 and 443 to the internet (for the certificate and the pages) and nothing else.
PCs set `RIFFLE_SYNC_URL=https://YOUR_DOMAIN`. Protection comes from the tokens (43 random
characters, hashes only on disk), HTTPS, a strict Content-Security-Policy, and a throttle of
20 failed sign-ins per address per five minutes. Anyone who learns a read token can read
everything, so give it to as few people as possible and `manage.py revoke` it if it leaks.
Prefer an IAP tunnel or Tailscale if only your own team needs access.

## Query with DuckDB

```bash
pip install duckdb google-cloud-storage
gcloud auth application-default login          # your own login; not needed on the VM
python infra/docker/riffle-data/server/duck.py --bucket YOUR_BUCKET --version 6.6.0 --split SPLIT_ID \
  -c "select repo, count(*) as prs from mined_dataset group by 1 order by 2 desc"
python infra/docker/riffle-data/server/duck.py --bucket YOUR_BUCKET --version 6.6.0 --split SPLIT_ID --db mined.duckdb   # then: duckdb mined.duckdb
```

It downloads the parquet files to `~/.cache/riffle-data` (only the ones that changed) and
makes three views named after the contracts: `mined_dataset`, `mined_snapshot` and
`mined_commit_features`. They stay three, with different grains: join them in SQL or with
`pipelines/training/frames.py`. Each view has a `filename` column naming the PC's file.
By default only PCs that finished (`run.json` says "batch done") are included, so a
half-mined PC does not skew a result; add `--include-unfinished` to see them. On the
store's own volume use `--local` instead of `--bucket`.

## Load into BigQuery (optional)

Not needed for the DuckDB route, and the BigQuery API is not enabled in the project
above. Enable `bigquery.googleapis.com` and give the VM's service account
`roles/bigquery.jobUser` and `roles/bigquery.dataEditor` first. Use it if several people
need SQL with per-person access, or dashboards.

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
`sync.py` against it, check which files go to which table, and build the DuckDB views
over real parquet files (skipped without `duckdb`, `pandas` and `pyarrow`). The GCS
and BigQuery calls themselves are not covered, because they need a real project.
