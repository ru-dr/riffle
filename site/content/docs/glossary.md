| Term | Meaning here |
| --- | --- |
| **Tenant** | One GitHub App installation. It has its own repository layers, data and metrics |
| **Rank band** | The three-way output: `senior_recommended`, `review_first`, `standard`. See [Rank bands](/docs/concepts/rank-bands) |
| **Holdout** | The share of pull requests left unranked, in FIFO order, to measure Riffle's lift against the queue you would otherwise have had |
| **Global model** | The gradient-boosted model that serves every repository from its first pull request. See [The model](/docs/concepts/model) |
| **Repository layer** | A lightweight per-repository adjustment fitted on that repository's mature outcomes |
| **Layer weight** | How much the repository layer counts. Starts at `0.0` and grows with mature labels |
| **Mature label** | An outcome whose observation window has closed, so it can be learned from. See [Labels and maturity](/docs/concepts/labels) |
| **Promotion** | Moving a newly trained model into serving after it beats its predecessor on the tenant's holdout |
| **Fallback** | Serving the global model alone when a repository layer is missing or underperforms |
| **Drift** | Feature distribution shift for a tenant, measured against the window its current model trained on |
| **Delivery ID** | GitHub's `X-GitHub-Delivery` header — the idempotency key for a pull request's whole journey |
| **Model version** | The pinned identity of everything that produced a score: model, layer, feature extractor and rules hash |
| **Shadow mode** | Scoring and learning without posting anything, for evaluating Riffle before it touches a queue |
