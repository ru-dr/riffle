# airflow

Airflow 3.3 for Riffle's scheduled pipelines. One Compose file runs it on a
laptop and on a single GCP VM; what differs between them is `.env`.

| Service | Role |
| --- | --- |
| `postgres` | Airflow's metadata database. Not the product's Postgres |
| `airflow-init` | Runs once: builds the image, migrates the database, writes the login file |
| `airflow-apiserver` | UI and API, on `AIRFLOW_BIND_ADDR:AIRFLOW_PORT` |
| `airflow-scheduler`, `airflow-dag-processor`, `airflow-triggerer` | Core Airflow processes |

It uses `LocalExecutor`: tasks run inside the scheduler container, so there are
no Celery workers or Redis. Heavy work (training, mining) must not run in these
containers. A DAG should start a Kubernetes Job, or call the code in
`pipelines/ingestion` or `pipelines/training`. DAGs live in
[`pipelines/dags`](../../../pipelines/dags) and are mounted read-only.

## Run locally

```bash
cd infra/docker/airflow
cp .env.example .env
```

Fill in the four secrets and the user logins in `.env`:

```bash
# AIRFLOW_FERNET_KEY
python -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
# AIRFLOW_API_SECRET_KEY, AIRFLOW_JWT_SECRET (run twice), POSTGRES_PASSWORD
python -c "import secrets;print(secrets.token_urlsafe(32))"
```

`POSTGRES_PASSWORD` may contain any character: the database URL is built by
`scripts/db_url.py`, which percent-encodes it.

```bash
docker compose up -d --build
docker compose ps          # wait for the apiserver and scheduler to be healthy (~1 min)
```

Open http://localhost:8080 and sign in as one of the users from `AIRFLOW_USERS`.
`riffle_smoke` is a check that the stack works; DAGs start paused, so unpause
it to run it.

## Users

Each person gets their own login, so the audit log shows who did what.

```bash
AIRFLOW_USERS=alice:admin,bob:viewer
AIRFLOW_PASSWORDS=alice:<generated>,bob:<generated>
```

Roles: `admin` (everything, including users' access to connections and
variables), `op` (run and manage DAGs), `user` (edit DAG runs), `viewer` (read
only). Give most teammates `op` or `viewer`. Every name needs a password, each at
least 12 characters and without `,` or `:`; the init step refuses to start
otherwise.

Entries are `name:value`, separated by commas; spaces around commas are
ignored and a trailing comma is fine. The init step checks every rule above and
stops with a message that names the entry (never the password).

To **add** someone, edit both lines in `.env` and run
`docker compose up -d --force-recreate`. To **remove** someone, also replace
`AIRFLOW_API_SECRET_KEY` and `AIRFLOW_JWT_SECRET` with new values first. Changing
only `AIRFLOW_PASSWORDS` recreates just the init service; the API server keeps
running with its old login file, and existing sessions stay valid until the API
secret changes, so without both steps a removed person may still be signed in.

Keep `AIRFLOW_FERNET_KEY` safe: it encrypts connections and variables in the
database, and losing it makes them unreadable.

## Run on a GCP VM

Use Debian or Ubuntu with at least 4 GB of memory (`e2-standard-2` is a
sensible start) and a 30 GB boot disk. Install Docker Engine and the Compose
plugin, then:

```bash
git clone git@github.com:ru-dr/riffle.git && cd riffle/infra/docker/airflow
cp .env.example .env     # same steps as above
sed -i "s/^AIRFLOW_UID=.*/AIRFLOW_UID=$(id -u)/" .env   # .env.example already has this line
docker compose up -d --build
```

- **Reaching the UI.** The default bind is `127.0.0.1`, so the UI is not
  exposed. Use an IAP tunnel and open http://localhost:8080:
  `gcloud compute ssh <vm> --tunnel-through-iap -- -L 8080:localhost:8080`.
  Set `AIRFLOW_BIND_ADDR=0.0.0.0` only behind a firewall rule limited to known
  addresses, or a TLS proxy. The simple auth manager sends no TLS of its own.
- **Restarts.** Containers use `restart: unless-stopped` and Docker starts on
  boot (`sudo systemctl enable docker`), so the stack returns after a reboot.
- **Credentials for GCP.** Attach a service account to the VM and use Application
  Default Credentials. Do not put key files in `.env` or the repo.
- **State.** The metadata database is the `postgres-data` Docker volume; logs
  are `airflow-logs`. Back up the volume, or move to Cloud SQL later by changing
  the URL that `scripts/db_url.py` prints.
- **Why a VM.** See [ADR 0013](../../../docs/adr/0013-airflow-on-a-compose-vm.md):
  what this is for, what moves to GKE later, and how secrets and backups work.

## Operate

```bash
docker compose logs -f airflow-scheduler
docker compose exec airflow-scheduler airflow dags list
docker compose down            # stop, keep data
docker compose down -v         # stop and delete the database and logs
```

After changing `requirements.txt` or the `Dockerfile`, run
`docker compose up -d --build`. DAG file changes need no restart.

To upgrade Airflow, set `AIRFLOW_VERSION` in `.env` (it feeds the image build and
its tag; the default is in `compose.yaml` and `Dockerfile`), read the release
notes, back up the database, then run `docker compose up -d --build`. The image
is built locally and never pulled; its name, `riffle-airflow:<version>-local`,
exists on no registry.

The helper scripts in `scripts/` have tests:
`python -m pytest infra/docker/airflow/tests`.

## Not done yet

- Logins use the simple auth manager: passwords sit in plain text in `.env` on
  the machine, and offboarding means editing it by hand. Use an SSO auth manager
  (Google sign-in) once the team grows.
- No TLS, and no Cloud SQL or Secret Manager integration.
- No DAGs beyond `riffle_smoke`. The expected ones are listed in
  [`pipelines/dags`](../../../pipelines/dags/README.md).
