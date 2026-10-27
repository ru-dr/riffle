# infra

How Riffle runs, per environment.

| Directory | Responsibility |
| --- | --- |
| [`docker`](docker) | Service images and the local Compose stacks (today: Airflow, the riffle-data team store) |
| [`k8s`](k8s) | Manifests for staging and prod |
| [`terraform`](terraform) | The team store VM (`store-vm`: store, Loki + Grafana, Airflow, bucket, IAM); later Postgres, Redis, Pub/Sub, GKE |
| [`loki`](loki), [`grafana`](grafana), [`alloy`](alloy) | The logging stack: where logs are stored, read, and collected from. One folder of configuration each; [`compose.logging.yaml`](compose.logging.yaml) runs the three together locally |

| Environment | Where | Notes |
| --- | --- | --- |
| `local` | Docker Compose | Emulated Pub/Sub, no GPU, stub explainer by default |
| `staging` | GKE, small pool | Test-org GitHub App, real Pub/Sub, one shared GPU node |
| `prod` | GKE | One shared GPU node for the explainer; training is CPU only |

## Local logging

Loki stores logs, Alloy collects them, Grafana shows them. Everything stays on this
machine: ports are bound to `127.0.0.1` and nothing is reported to Grafana Labs.

```bash
cd infra
cp .env.example .env            # then set GF_SECURITY_ADMIN_PASSWORD in .env
docker compose -f compose.logging.yaml up -d
docker compose -f compose.logging.yaml ps       # alloy and grafana show "(healthy)"; loki just shows "Up"
curl http://localhost:3100/ready                # Loki: "ready" (it says 503 for ~15 seconds after start)
```

Loki has no health status of its own: its image has no shell or curl for a healthcheck to
use. The `curl` above is how you check it.

Open http://localhost:3000, sign in with the login from `.env`, and go to **Explore**
(the Loki data source is already set up). Logs from every Docker container appear on their
own. A command run on the host, outside Docker, appears once it writes a `*.log` or
`*.jsonl` file under `<repo>/logs/`:

```bash
mkdir -p logs/aidev
python -m aidev_ingest download 2>&1 | tee logs/aidev/download.log
```

| Page | Address |
| --- | --- |
| Grafana | http://localhost:3000 |
| Loki (API) | http://localhost:3100/ready |
| Alloy (is the pipeline healthy?) | http://localhost:12345 |

```bash
docker compose -f compose.logging.yaml down        # stop; the logs are kept
docker compose -f compose.logging.yaml down -v     # stop and delete the logs and Grafana's data
```

Logs are kept 7 days (`LOKI_RETENTION` in `.env`). Each component's folder explains what it
does and how to change it. Config checks that need no containers:
`python -m pytest infra/tests/test_logging_stack.py`.
