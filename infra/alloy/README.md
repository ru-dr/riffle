# alloy

[Grafana Alloy](https://grafana.com/docs/alloy/latest/) collects logs and sends them to
Loki. This folder holds its pipeline, [`config.alloy`](config.alloy); the container is
defined in [`../compose.logging.yaml`](../compose.logging.yaml). Loki does not read logs
by itself: without Alloy it would be empty. (Alloy replaces Promtail, which Grafana has
deprecated.)

## What it collects

| Source | How | Labels you get |
| --- | --- | --- |
| Every Docker container on this machine | Through the Docker socket | `job="docker"`, `container`, `service` and `compose_project` (the last two for Compose containers), `stream` (stdout or stderr) |
| Files in the host drop folder | Tails `*.log` and `*.jsonl` | `job="host"`, `filename` |

The drop folder is `<repo>/logs/` (git ignores it; change it with `RIFFLE_LOGS_DIR`). A
command run on the host appears in Grafana once it writes there:

```bash
mkdir -p logs/aidev
python -m aidev_ingest download 2>&1 | tee logs/aidev/download.log
```

Then in Grafana: `{job="host"}`, or `{job="host", filename=~".*download.log"}`.

## Things to know

- **The Docker socket is powerful.** It is mounted read-only, but anything that can talk to
  Docker's API can control Docker. On your own machine that is acceptable. Do not copy this
  mount to a shared machine without thinking again; on a server, ship container logs from
  Docker's log files or a logging driver instead.
- **It remembers how far it has read** in the `alloy-data` volume, so a restart does not
  send everything again. `docker compose down -v` forgets that, and a new start re-reads
  the files from the beginning (Loki still refuses lines older than a week).
- **Its own page** is at http://localhost:12345: each part of the pipeline, and whether it
  is healthy. The first place to look when logs are missing.
- **Its health** shows in `docker compose ps`. The check is a few lines of `bash` asking Alloy's own `/-/ready`
  page, because its image has no `curl` or `wget`.
- **Its own logs** are collected like any other container's.
- **No usage reports** are sent (`--disable-reporting`).

## Changing it

Edit `config.alloy`, then `docker compose -f compose.logging.yaml restart alloy`. To add a
source, add a `loki.source.*` block that ends with
`forward_to = [loki.write.local.receiver]`. A mistake in the file stops Alloy from starting
and is printed in `docker compose logs alloy`.
