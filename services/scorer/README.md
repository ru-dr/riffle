# scorer (Python)

Consumes `PrEvent`, extracts features, ranks with the tenant model, asks the
explainer for a sentence, writes `ScoreResult`.

Invariants: idempotent per delivery ID; feature extractor and model version
resolved together and pinned per request; fail open — if scoring fails the PR
still reaches the queue, unranked and flagged.

```bash
uv sync && uv run pytest
```
