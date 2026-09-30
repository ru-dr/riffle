Riffle is four independently deployable services. Each boundary is a place
where the failure mode changes — which is why they are separate processes, and
why no service reaches into another's storage.

| Service | Language | Owns | Allowed to fail? |
| --- | --- | --- | --- |
| `intake` | Go | Webhook verification, deduplication, publishing | No — GitHub allows 10 seconds |
| `scorer` | Python | Features, ranking, writing the result | No — an unranked PR is a broken promise |
| `explainer` | Python | LLM inference behind an API | Yes — degrades quality only |
| `app` | TypeScript | GitHub App and dashboard | No |

Everything that crosses a boundary is defined in [contracts](/docs/architecture/contracts).

## intake

The webhook front door. It does four things and nothing else: verify the
GitHub signature, deduplicate on `X-GitHub-Delivery`, publish a `PrEvent`, and
return `200` inside ten seconds. No database writes, no feature extraction, no
outbound calls to GitHub.

## scorer

Consumes `PrEvent`, extracts features, ranks with the global model plus the
repository layer, asks the explainer for a sentence, and writes a
`ScoreResult`. The feature extractor and model version are resolved together
and pinned per request. If scoring fails, the pull request still reaches the
queue, unranked and flagged.

## explainer

Turns a feature vector and a band into one sentence a reviewer can act on.
Warm GPU, a response cache in Redis, a per-tenant rate limit, and a hard
timeout that falls back to a deterministic template built from the features
alone.

## app

The only service that talks to GitHub or to people: installation callbacks,
check runs, pull request comments, and the queue view. It reads `ScoreResult`;
a result with a `null` explanation renders without one and is still a valid
rank.

## Supporting infrastructure

| Component | Used for |
| --- | --- |
| Postgres | Features, tenants, outcomes |
| Redis | Idempotency keys, inference cache |
| Pub/Sub | The event bus between intake and scorer |
| Airflow | Scheduled retraining |
| MLflow | Model registry |
| Kubernetes Jobs | Per-tenant training, under a bounded pool |
