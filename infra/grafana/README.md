# grafana

[Grafana](https://grafana.com/docs/grafana/latest/) is where you read the logs. This
folder holds what Grafana loads when it starts, under [`provisioning/`](provisioning); the
container is defined in [`../compose.logging.yaml`](../compose.logging.yaml).

## Use it

Open http://localhost:3000 and sign in as `GRAFANA_ADMIN_USER` with the
`GF_SECURITY_ADMIN_PASSWORD` you put in `infra/.env`. Go to **Explore**; the **Loki** data
source is already selected.

```logql
{job="docker"}                                  every container
{service="grafana"}                             one Compose service
{job="host"}                                    files from the drop folder
{job="docker"} |= "error"                       lines containing "error"
{job="docker"} | json | level="error"           lines that are JSON with level=error
sum by (service) (rate({job="docker"}[5m]))     lines per second, per service
```

## What is set up for you

- **The Loki data source** is defined in
  [`provisioning/datasources/loki.yaml`](provisioning/datasources/loki.yaml), so it is
  there on first login and cannot be edited away in the UI. Change the file instead.
- **No sign-up and no anonymous access.** Accounts are made by an admin.
- **No usage reports and no update checks** are sent to Grafana Labs.
- **Your own dashboards and settings** are kept in the `grafana-data` volume. The admin
  account is created the first time Grafana starts; changing `GF_SECURITY_ADMIN_PASSWORD` in
  `.env` afterwards does not change it. Change the password in the UI, or run
  `docker compose -f compose.logging.yaml down -v` to start again (this also deletes the logs).

## Adding a dashboard that lives in the repository

Put its JSON in a new folder, add a provider file under `provisioning/dashboards/`, and
mount the folder in `compose.logging.yaml`. None exists yet: Explore is enough while the
logs are few.
