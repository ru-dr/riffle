Every pull request crosses four services. Each boundary is a place where the
failure mode changes, which is why they are separate processes rather than
modules of one.

```text
GitHub ──webhook──▶ intake ──PrEvent──▶ scorer ──▶ app ──▶ review queue
                     │                    │  ▲
                 dedupe by           explain│  │ sentence, or null
                 delivery ID                ▼  │
                                         explainer
```

## One pull request, end to end

| Step | Where | What happens | Budget |
| --- | --- | --- | --- |
| 1 | `intake` | Verifies the webhook signature, deduplicates by delivery ID, publishes a `PrEvent` | 10 s, GitHub's limit |
| 2 | `scorer` | Extracts features, runs the global model plus the repository layer, writes a `ScoreResult` | Never lost |
| 3 | `explainer` | Turns the feature vector and band into one sentence | May time out |
| 4 | `app` | Places the PR in its band and posts the explanation | The only human surface |

## Why the boundaries fall where they do

**Intake must never be slow.** GitHub allows ten seconds for a webhook
response, so intake does four things and nothing else: verify, deduplicate,
publish, return `200`.

**Scoring must never be lost.** An unranked pull request is a broken promise,
so scoring is idempotent: the same delivery ID can never produce two scores.

**Explanation is allowed to fail.** The explainer runs an LLM behind a hard
timeout with a deterministic template fallback. When it is slow or down, the
`explanation` field is `null` and the rank is still valid.

## What each pull request produces

```json
{
  "delivery_id": "8f2c…a91",
  "pr_number": 2841,
  "risk_score": 0.81,
  "rank_band": "senior_recommended",
  "model_version": "v7",
  "explanation": "Touches a path this repository has reverted twice this quarter."
}
```

The full shape is on [Contracts](/docs/architecture/contracts).
