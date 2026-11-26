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

The latency alert reads `riffle_intake_latency_seconds`, which exists once intake runs and is
scraped by Managed Prometheus; until then the policy simply waits.

Each alert's text says what it means and where to look first.

| Input | |
| --- | --- |
| `alert_emails` | Where alerts go; empty means they only show in the Cloud Monitoring console |
| `backlog_age_seconds`, `intake_p99_seconds` | Thresholds |
| `ingress_ip` | From the edge module |

Not here yet: an alert on the share of PRs left unranked, and error tracking. Both need
metrics and exceptions from the services, which do not exist yet.
