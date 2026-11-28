# services

Six independently deployable services. Each boundary is a place where the
failure mode changes — that is why they are separate processes and not modules.

| Service | Language | Responsibility | Allowed to fail? |
| --- | --- | --- | --- |
| [`intake`](intake) | Go | Verify, dedupe, publish, 200 inside 10s | No — GitHub gives us 10 seconds |
| [`pr-miner`](pr-miner) | Python | The PR as a row of the miner's dataset, made by the miner's code (ADR 0027); not built yet | Slow is fine (seconds to minutes per PR); a failure is retried |
| [`scorer`](scorer) | Python | History, model, ranking, write the result | No — an unranked PR is a broken promise |
| [`scorelog-writer`](scorelog-writer) | Python | The score log: `score-log` to Parquet per tenant and day, encrypted with the tenant's key (ADR 0024, 0028) | Yes — rows wait in the queue; scores are not affected |
| [`explainer`](explainer) | Python | LLM inference behind an API | Yes — degrades quality only |
| [`app`](app) | TypeScript | GitHub App and dashboard | No |

Everything crossing a service boundary is defined in [`contracts/`](../contracts).
No service reaches into another's storage.
