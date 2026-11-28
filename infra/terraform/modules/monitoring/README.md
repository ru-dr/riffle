# monitoring

The uptime check and alerts (diagram: "Uptime checks", "Alertmanager", "On-call"). On GKE the
alerting is Cloud Monitoring's: it runs PromQL over Managed Prometheus (`riffle_*` metrics)
and over Google's own metrics (Pub/Sub), and notifies the email channels given. There is no
Alertmanager to run; Slack or a pager can be added as more notification channels.

| Alert | Fires when | Severity |
| --- | --- | --- |
| intake unreachable | The uptime check of `http://<ingress IP>/healthz` fails for 2 minutes | Critical |
| messages in dead letter | Any `<name>-*-dead-letter-hold` subscription holds a message for 5 minutes | Error |
| queue falling behind | Any subscription's oldest unacknowledged message is older than 5 minutes, for 5 minutes | Warning |
| intake slow | intake's p99 latency is above 2 s for 10 minutes (GitHub gives up at 10 s) | Warning |
| intake cannot publish | intake answered GitHub 503 (`publish_failed`) in the last 10 minutes; GitHub does not redeliver by itself | Error |
| scorer has no model | PRs are stored unranked with `model_unavailable` for 10 minutes (nothing registered as `champion`, or MLflow down) | Error |
| scorer failing PRs | More than 5% of the PRs it should rank end `scoring_failed` or `invalid_input`, for 15 minutes | Error |
| scorer cannot store or publish | Messages are redelivered because Postgres or Pub/Sub failed, for 10 minutes | Error |
| score log rows dropped | `riffle_score_log_dropped_total` rose (the score still posted, ADR 0024) | Warning |
| scorer slow | The scorer's p95 time per ranked PR is above 5 s for 15 minutes | Warning |
| score log files not written | The score-log writer failed to write files for 15 minutes (tenant key, KMS or bucket); rows wait in the queue | Warning |

The intake and scorer alerts read the services' own metrics (`riffle_intake_*`, `riffle_scorer_*`,
`riffle_score_log_dropped_total`), which exist once the services run and are scraped by Managed
Prometheus; until then those policies simply wait. Not-outside-contributor PRs are unranked by
design and never count as failures.

Each alert's text says what it means and where to look first.

| Input | |
| --- | --- |
| `alert_emails` | Where alerts go; empty means they only show in the Cloud Monitoring console |
| `backlog_age_seconds`, `intake_p99_seconds`, `scorer_failed_share`, `scorer_p95_seconds` | Thresholds |
| `ingress_ip` | From the edge module |

Not here yet: error tracking (exceptions grouped across services), and alerts for the services
that are not built (pr-miner, app, explainer). The dashboard is in Grafana: `infra/k8s/platform/dashboards`.
