# intake (Go)

Webhook front door. Verify signature, dedupe by delivery ID, publish, return
200 inside 10 seconds. **Nothing else** — no database writes, no feature
extraction, no outbound GitHub calls.

Invariant: the same delivery ID must never be published twice.

## What it does with a delivery

| Step | On failure |
| --- | --- |
| Read the body (GitHub's limit is 25 MB) | 413 |
| Check `X-Hub-Signature-256` (HMAC-SHA256 of the body with the webhook secret, compared in constant time) | 401, nothing published |
| Check the `X-GitHub-Event` and `X-GitHub-Delivery` headers | 400 |
| Map the payload to messages (`internal/mapping`) | 400 if malformed; 202 `ignored` if nothing needs it |
| Claim the delivery in Redis (`SET NX`, pending for 5 minutes) | Already done: 200 `duplicate`. Being published by another request: 202 `in_flight`. Redis down: publish anyway |
| Publish each message and wait until Pub/Sub has stored it | 503; the claim is released so a redelivery is published |
| Mark the delivery done (remembered 72 hours) | Logged only |

| GitHub event | Published | Contract |
| --- | --- | --- |
| `pull_request`: opened, synchronize, reopened, ready_for_review, edited, labeled | One `PrEvent` on `pr-events` (read by the pr-miner, which makes the model's row, ADR 0027) | [`pr_event`](../../contracts/pr_event.schema.json) |
| `check_suite`: completed | One `PrEvent` (`ci_completed`) per PR in the suite; with several PRs the delivery id gets `:<pr_number>` | `pr_event` |
| `installation`: created, deleted, suspend, unsuspend, new_permissions_accepted; `installation_repositories`: added, removed | One `InstallationEvent` on `installation-events` (ADR 0025) | [`installation_event`](../../contracts/installation_event.schema.json) |
| `ping` | Nothing; answers `pong` | |
| Anything else | Nothing; 202 `ignored` | |

Ordering keys: `<tenant_id>/<repo>#<pr_number>` for `PrEvent`, the `tenant_id` for
`InstallationEvent`, so one PR's, or one installation's, events arrive in order. Every message
carries the attributes `delivery_id`, `tenant_id` and `schema` (e.g. `pr_event/1.0`).

Why Redis being down does not stop publishing: a PR that is never ranked breaks the product's
promise, while a duplicate is caught again by the scorer, which is idempotent on `delivery_id`
(`score_results` has it as its key).

Known limits:
- A check suite on a fork lists no PRs, and finding them would need a GitHub call, which intake
  never makes. Those PRs are rescored on their next push.
- Outcome events for the labeller (`outcome-events`: closed PRs, pushes) are not published yet;
  their contract (`outcome_event`) comes with the labeller.
- GitHub does not redeliver a failed delivery by itself; it can be redelivered from the App's
  settings or its API. A 503 still records the failure on GitHub's side.

## Configuration

In the cluster every value comes from `riffle-platform-settings` (made by Terraform) or the
`intake-secrets` Secret (External Secrets, from Secret Manager); see `infra/k8s/README.md`.

| Variable | Source in the cluster | Default |
| --- | --- | --- |
| `WEBHOOK_SECRET` | `intake-secrets` | required |
| `PUBSUB_PROJECT_ID` | settings | required |
| `TOPIC_PR_EVENTS`, `TOPIC_INSTALLATION_EVENTS` | settings (e.g. `riffle-staging-pr-events`) | required |
| `REDIS_HOST`, `REDIS_PORT` (or `REDIS_ADDR`) | settings (Memorystore, port 6378) | `localhost:6379` |
| `REDIS_AUTH` | `intake-secrets` | empty |
| `REDIS_CA_CERT` | settings; when set, Redis is reached over TLS checked against it | empty (plain) |
| `INTAKE_ADDR`, `METRICS_ADDR` | — | `:8080`, `:9090` |
| `INTAKE_DEADLINE` | — | `8s` (must stay below GitHub's 10 s) |
| `DEDUPE_PENDING_TTL`, `DEDUPE_DONE_TTL` | — | `5m`, `72h` |
| `PUBSUB_EMULATOR_HOST` | — | unset (set it to use the Pub/Sub emulator) |

Google credentials come from Workload Identity (the pod runs as the `intake` service account);
there is no key file.

Ports: `8080` serves `POST /webhook`, `GET /healthz`, `GET /readyz` (fails during shutdown); the
load balancer reaches only this one. `9090` (named `metrics`) serves `GET /metrics`
(`riffle_intake_events_total{tenant,event,result}`, `riffle_intake_latency_seconds{tenant,result}`).
Logs are JSON lines on stdout with `delivery_id` and `tenant_id`.

## Build, test, deploy

```bash
go test -race ./...    # unit tests: in-memory Redis (miniredis) and Pub/Sub (pstest); messages checked against contracts/
```

| Step | Where |
| --- | --- |
| Image | `.github/workflows/images.yml` builds it and, on `main`, pushes `…/intake:sha-<commit>` |
| Deploy | `infra/k8s/apps/staging/20-intake.yaml`, with the shared chart `infra/k8s/charts/riffle-service`; put the new tag in `image.tag` |
| Test without the GitHub App | Sign a payload from `fixtures/webhooks/` with the webhook secret and POST it to `/webhook` (below) |

```bash
SECRET=$(gcloud secrets versions access latest --secret=riffle-staging-github-webhook-secret)
BODY=fixtures/webhooks/pull_request.opened.json
SIG="sha256=$(openssl dgst -sha256 -hmac "$SECRET" < "$BODY" | sed 's/^.* //')"
kubectl -n riffle-serving port-forward svc/intake 8080:8080 &
curl -s -X POST localhost:8080/webhook -H "X-GitHub-Event: pull_request" \
  -H "X-GitHub-Delivery: test-$(date +%s)" -H "X-Hub-Signature-256: $SIG" --data-binary @"$BODY"
# → {"status":"published","messages":1}
```

| Folder | |
| --- | --- |
| `cmd/intake` | Configuration, wiring, graceful shutdown, the `healthcheck` subcommand used by the probes |
| `internal/webhook` | Signature check |
| `internal/mapping` | GitHub payload to `PrEvent` / `InstallationEvent` |
| `internal/dedupe` | Claim, done and release in Redis |
| `internal/publish` | Pub/Sub publishing with ordering keys, to the topic names in GCP |
| `internal/server` | The HTTP handlers and metrics |
