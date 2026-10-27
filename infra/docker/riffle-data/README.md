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
names from the file's own schema. LIKE patterns are escaped. A DuckDB error is a 422, an unreadable
parquet file is skipped in a table that spans PCs, and at most 2 parquet connections run at once (a
third request gets 503 until one is free). Big files that live only in the bucket are downloaded to
`<data>/cache` the first time they are opened; the cache is trimmed to `RIFFLE_CACHE_MAX_BYTES`
(default 20 GiB), keeping every file used in the last 10 minutes. Versions sort numerically
(`6.10.0` after `6.9.0`).

**A run that did not finish is not shown as done.** The miner sends `batch done` only when every
file was sent or skipped; otherwise it sends `batch incomplete` with `files_failed` and
`files_deferred`, and the server rewrites such a run to `batch incomplete`. `batch aborted` shows
as stopped on the admin and run pages. `duck.py`, `bq_load.py` and the catalogue count a PC as
finished only by `tables.finished(status)`.

## Tokens: who can do what

| Kind | Made with | Can |
| --- | --- | --- |
| **admin** (`ad_…`) | `manage.py add-admin NAME` | issue, rotate and revoke PC tokens, add readers; reads nothing, writes no files. In the lead's `.env` as `RIFFLE_ADMIN_TOKEN`, and the sign-in of the admin page |
| **PC** (`rw_…`) | `build_dataset.py … --shard-plan N --issue-tokens` (or `manage.py issue-split ID N`) | write under its own name, and only under its own split; optionally expires |
| **reader** (`rd_…`) | `manage.py add-reader NAME` | read the pages and the APIs |

The normal flow: the lead plans the split with `--shard-plan N --issue-tokens`, which calls
`POST /v1/admin/splits`, and sends each PC its join code privately. The PUT of a `run.json` runs under a lock, and a PUT is refused with 409 when a file and a directory would share one path. A PC runs `python join.py CODE`
(see the miner's README). Planning the same split again rotates its tokens; `POST /v1/admin/revoke`
with a `split_id` or a `name` (or `manage.py revoke-split ID`) cuts access at once, and
`GET /v1/admin/pcs` shows each token's split, expiry and last upload. Only hashes are stored.

## The admin page (`/admin`)

A second sign-in, with an **admin** token only (a read token is refused; an admin token reads no data itself, but it can add readers, who can). It shows every PC token with its state (mining, done, stopped, silent, never uploaded, expired), progress, expiry and last upload, and the splits that were planned. From it the lead can:

- **Revoke** one PC, or **Revoke all** of a split (press twice to confirm).
- **Rotate** a split: new tokens for every PC, the old ones stop at once, and the new join codes are shown once with Copy buttons. The page rebuilds the codes from the split's saved flags, so it does not need the repos file.
- **Add or revoke a reader** (the token is shown once).
- **Edit a split's settings** (Edit settings in its row opens a panel below the table): every shared setting as a toggle, a choice or a field, grouped as What is mined, LLM flags, Feature groups, Re-mining and Split and files. On/off settings are Default / On / Off, so a setting you leave alone sends no flag. A list of what changed shows before Save, with a warning when the change alters the mined rows. "All settings as flags" (collapsed) shows and edits the same thing as text. The form is built from `static/settings-schema.json`, which `pipelines/ingestion/repo_miner/settings_schema.py --write` makes from the miner's own parser, and a test keeps them equal. Save calls `POST /v1/admin/split-settings` and gives the new settings version; no token or join code is re-issued, and each PC reads the change when it next starts a batch, keeping its machine flags. The store checks the same list: a flag the miner does not share, a wrong value, a setting given twice and the planning flags (`--shard-plan`, `--issue-tokens`, `--tokens-out`, `--token-days`) are refused with 400. **Split and files** (repos file, only-domain, split-by, output names) are locked: changing them would move repos between PCs or rename files, so that takes a new plan. Settings are stored in the open: no tokens or passwords.
- See which admin tokens exist (they are made on the VM with `manage.py add-admin`).

A new split is still planned from the miner (TUI Lead tools, F3), because the plan needs the repos file. The admin session is its own cookie: a signed 8-hour session (`tokens.session_make` and `session_who`) keyed on the admin's stored hash, so revoking that admin ends it. Calls from it must carry `X-Requested-With`, and the page has no way to read data. The last admin cannot be revoked (the check runs under the token lock and answers 409).

## Backup and restore

`manage.py reindex` rebuilds the index from file times (it makes no activity points) and indexes files that exist only in the bucket, using the bucket listing. Pulling a JSON file from the bucket checks that it parses before it replaces the local copy, and `restore` downloads to a `.part` file and renames it.

The only state that lives just on the VM disk is `tokens.json` (hashes and each split's saved flags). Every change to it is copied to the bucket as `_system/tokens.json`. Everything else is already in the bucket. To rebuild after losing the disk: create the VM again with `gcp/up.sh` (a new empty disk restores the tokens by itself when the service starts), then

```bash
gcloud compute ssh riffle-data --project=riffle-data-miner --zone=us-central1-a \
  --command='cd /opt/riffle-data && sudo docker compose exec -T riffle-data python manage.py restore'
```

which puts the logs, health files and `run.json` files back on the disk and rebuilds the index. Datasets stay in the bucket and are read from it.

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

The repeatable way is Terraform: [`infra/terraform/store-vm`](../../terraform/store-vm) builds the bucket, service account, address, firewall and a VM that runs this store, Loki + Grafana and Airflow (ADR 0018). The `gcloud` steps and `gcp/up.sh` below are the manual route and the one the first VM was made with.

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
20 failed sign-ins per address per five minutes. The throttle counts distinct failing tokens, so a valid token still passes while an address is blocked, and `whoami` and `/v1/split` are throttled too. Anyone who learns a read token can read
everything, so give it to as few people as possible and `manage.py revoke` it if it leaks.
Prefer an IAP tunnel or Tailscale if only your own team needs access.

## Search and chart run traces (Loki, optional)

```bash
docker compose -f compose.yaml -f compose.logs.yaml up -d      # adds Loki and Grafana; set GRAFANA_ADMIN_PASSWORD in .env first
```

The miner's trace is the one log. The store keeps each trace file as before and forwards every new line to Loki as it arrives (`server/loki.py`);
nothing tails the disk, and PCs never contact Loki (ADR 0017). With Loki set up:

- the **Log search** page reads Loki (the files are the fallback, shown without "From Loki." in its note);
- the **admin page** has a Run tracking panel: whether Loki is receiving, lines sent, what is queued, the last error, and links to Grafana and Airflow;
- Grafana, http://127.0.0.1:3000 (user `admin`), has the "Riffle miner" dashboard, or Explore, for example
  `{job="riffle-miner", miner_stage="szz"} | json | msg="done"`. Labels: `pc`, `miner_version`, `split`, `run`, `lvl`, `component`, `miner_stage`; `repo` and `pr` are structured metadata. Traces are kept 90 days.

A watched run uploads only its new trace lines every 10 s (`X-Append-Offset`); the bucket copy catches up every 5 minutes and when the run ends.
If Loki is down the store goes on; it sends what Loki missed from the file when Loki is back (`loki_offsets` in `index.db`).

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
half-mined PC does not skew a result; add `--include-unfinished` to see them. The split's PC count is part of the plan: `--n N` keeps only the PC directories of an N-PC split, and without it a directory of another N is left out (old data stays on disk). An unsharded `dataset.parquet` is recognised too. On the
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
names) from every PC's parquet files, replacing each table, so rerun it as PCs finish. Each table is first loaded into a staging table (`<name>__loading`) and replaces the live one only after every load succeeded; a table that now has no files is deleted. `--n` works as for DuckDB.
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
