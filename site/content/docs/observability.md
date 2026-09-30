Every metric is prefixed `riffle_` and always carries a `tenant` label. Every
log line is structured JSON and always carries `delivery_id`, which traces one
pull request across all four services.

## Metrics

| Metric | Meaning | Watch for |
| --- | --- | --- |
| `riffle_intake_latency_seconds` | Webhook received to `200` returned | Anything near GitHub's 10 s budget |
| `riffle_scoring_duration_seconds` | Event received to result written | Sustained growth: the queue is backing up |
| `riffle_explainer_timeouts_total` | Explanations degraded to the template | A step change: the explainer is slow or down |
| `riffle_queue_depth` | Events waiting to be scored | Drives KEDA autoscaling |
| `riffle_tenant_model_auc` | Per-tenant ranking quality over time | A tenant falling below the global model |
| `riffle_drift_score` | Feature distribution shift per tenant | A tenant whose code has moved away from its training window |

## Following one pull request

Filter every service's logs on one delivery ID to see its whole journey:

```json
{"level":"info","service":"intake","delivery_id":"8f2c…a91","tenant":"4192","msg":"published pr_event","latency_ms":41}
{"level":"info","service":"scorer","delivery_id":"8f2c…a91","tenant":"4192","msg":"scored","risk_score":0.81,"rank_band":"senior_recommended","model_version":"v7"}
{"level":"warn","service":"explainer","delivery_id":"8f2c…a91","tenant":"4192","msg":"timeout, template fallback","elapsed_ms":900}
```

## Health signals

| Signal | Healthy | Meaning when it is not |
| --- | --- | --- |
| Intake latency, p99 | well under 10 s | GitHub will retry, and duplicates rise |
| Holdout lift | ranked queue beats FIFO | The ranking is not helping; check drift and layer weights |
| Explainer timeouts | rare and flat | Quality is degraded; ranks are still valid |
