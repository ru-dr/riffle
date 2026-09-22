# services

Four independently deployable services. Each boundary is a place where the
failure mode changes — that is why they are separate processes and not modules.

| Service | Language | Responsibility | Allowed to fail? |
| --- | --- | --- | --- |
| [`intake`](intake) | Go | Verify, dedupe, publish, 200 inside 10s | No — GitHub gives us 10 seconds |
| [`scorer`](scorer) | Python | Features, ranking, write the result | No — an unranked PR is a broken promise |
| [`explainer`](explainer) | Python | LLM inference behind an API | Yes — degrades quality only |
| [`app`](app) | TypeScript | GitHub App and dashboard | No |

Everything crossing a service boundary is defined in [`contracts/`](../contracts).
No service reaches into another's storage.
