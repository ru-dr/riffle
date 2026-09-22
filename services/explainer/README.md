# explainer (Python)

LLM inference behind an API. Warm GPU, cached, rate-limited, with a hard
timeout to a template fallback.

Invariant: **the LLM is never on the correctness path.** This service failing
means an explanation is missing, nothing more.

```bash
uv sync && uv run pytest
```
