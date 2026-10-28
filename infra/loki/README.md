# loki

[Grafana Loki](https://grafana.com/docs/loki/latest/) stores the logs. This folder holds
its configuration, [`config.yaml`](config.yaml); the container is defined in
[`../compose.logging.yaml`](../compose.logging.yaml). See [`../README.md`](../README.md)
for how to run the whole logging stack.

Loki indexes only a few **labels** per log stream (`container`, `service`, `job`, ...),
not the text of each line. You pick a stream by its labels, then filter the lines in it.
That keeps it small enough for a laptop.

## What this configuration does

- **One process, local disk.** Logs live in the `loki-data` Docker volume. Nothing is
  replicated and no object store is used.
- **Keeps 7 days** by default. Set `LOKI_RETENTION` in `infra/.env` (`168h`, `720h`, `7d`;
  at least `24h`). The compactor deletes older data every few minutes, after a 2 hour delay.
- **Refuses lines older than a week** when they arrive. If Alloy first reads a log file
  that already holds older lines, those lines are skipped, and Loki says so in its log.
- **No login**, because it is published to `127.0.0.1` only. Do not change
  `LOGGING_BIND_ADDR` to `0.0.0.0` without a firewall in front.
- **No usage reports** to Grafana Labs.

## Is it ready?

`docker compose ps` shows Loki as "Up", not "healthy": its image has no shell, `wget` or
`curl`, so a healthcheck has nothing to run. Ask it from the host instead:

```bash
curl http://localhost:3100/ready      # "ready"; HTTP 503 "waiting for 15s" for a short while after start
```

Alloy and Grafana start after Loki and cope with that wait: Alloy retries its pushes, and
Grafana's data source reports an error until Loki is ready. A few lines such as
`error getting ingester clients err="empty ring"` at start-up are that same wait, not a fault.

## Ask it a question without Grafana

```bash
# Is it up?
curl http://localhost:3100/ready

# The last 10 lines from one Compose service, over the last hour
curl -G http://localhost:3100/loki/api/v1/query_range \
  --data-urlencode 'query={service="loki"}' \
  --data-urlencode 'limit=10' \
  --data-urlencode "start=$(date -d '1 hour ago' +%s)000000000"
```

## Changing it

Edit `config.yaml`, then `docker compose -f compose.logging.yaml restart loki`. Check a
change without starting anything:

```bash
docker run --rm -v "$PWD/loki/config.yaml:/c.yaml:ro" grafana/loki:3.7.9 \
  -config.file=/c.yaml -config.expand-env=true -verify-config
```

The image version is pinned in `compose.logging.yaml`. Read the Loki release notes before
raising it: the storage schema (`schema_config`) must stay readable by the new version.

## Not here yet

Object storage, several replicas, authentication and alerting rules. This is the local
setup; the staging and production setup is a separate piece of work.
