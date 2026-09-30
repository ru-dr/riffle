These are not preferences. Breaking one is a bug even if the tests pass.

| # | Invariant | What it means |
| --- | --- | --- |
| 01 | Idempotent scoring | The same delivery ID must never produce two scores |
| 02 | Version pinning | A request never mixes a new model with an old feature extractor |
| 03 | Tenant fairness | One monorepo pushing 500 PRs an hour must not starve a small team |
| 04 | Training backpressure | Bounded job pool; excess requests queue or defer, never run |
| 05 | Fail open, not closed | If scoring fails, the PR still appears, unranked and flagged |
| 06 | Explanation is optional | The explainer's failure degrades quality only |

## How each one is held

**Idempotent scoring.** `delivery_id` — GitHub's `X-GitHub-Delivery` header —
is the idempotency key end to end, and must survive every hop.

**Version pinning.** `model_version` is resolved once per request and carried
with the score, covering the model, the repository layer, the feature
extractor and the compiled rules hash.

**Tenant fairness.** Queues are per tenant, and autoscaling is driven by
`riffle_queue_depth`, so one noisy installation cannot drain capacity from the
rest.

**Training backpressure.** Per-tenant training runs as Kubernetes Jobs under a
bounded pool. That bound is a product invariant, not a cost setting.

**Fail open.** A pull request is never lost from the queue because scoring
failed; it appears unranked, with a flag, and is retried.

**Explanation is optional.** `explanation` is nullable by design. A `null`
means the explainer was slow or down — the rank is still valid.
