<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset=".github/assets/logo-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset=".github/assets/logo-light.svg">
  <img src=".github/assets/logo-light.svg" alt="Riffle" width="300">
</picture>

### Every repo breaks differently.

Riffle orders a team's pull request queue by risk — trained on your
repository's own revert history, not one vendor's rules. Self-hosted, open source, and nothing skips review.

<p>
  <img alt="status: in development"
    src="https://shieldcn.dev/badge/status-in%20development-18181b.svg?color=18181b&labelTextColor=a1a1aa&labelOpacity=1&height=22&fontSize=11&radius=4&padX=9&font=geist-mono&valueColor=f5b544&logo=lu:Hammer&logoColor=f5b544">
  <img alt="stack: Go, Python, TypeScript"
    src="https://shieldcn.dev/badge/stack-Go%20%C2%B7%20Python%20%C2%B7%20TypeScript-18181b.svg?color=18181b&labelTextColor=a1a1aa&labelOpacity=1&height=22&fontSize=11&radius=4&padX=9&font=geist-mono&valueColor=e4e4e7&logo=lu:Layers&logoColor=a1a1aa">
  <img alt="deploy: self-hosted"
    src="https://shieldcn.dev/badge/deploy-self--hosted-18181b.svg?color=18181b&labelTextColor=a1a1aa&labelOpacity=1&height=22&fontSize=11&radius=4&padX=9&font=geist-mono&valueColor=7dd3fc&logo=lu:Server&logoColor=7dd3fc">
  <img alt="license: Apache-2.0"
    src="https://shieldcn.dev/badge/license-Apache--2.0-18181b.svg?color=18181b&labelTextColor=a1a1aa&labelOpacity=1&height=22&fontSize=11&radius=4&padX=9&font=geist-mono&valueColor=e4e4e7&logo=apache&logoColor=e8604c">
</p>

</div>

---

## The problem

AI coding agents made writing code cheap. Reviewing it did not get cheaper.
The bottleneck moved from authoring to verification, and reviewer capacity
did not scale to meet it.

| Source | Finding |
| --- | --- |
| Faros AI, *The Acceleration Whiplash* | Median review time up **441.5%**; bugs per developer up 54%; **31% more PRs merging with no human review** |
| LinearB, *2026 Benchmarks* | Across 8.1M PRs: AI-generated PRs wait **4.6x longer** for first review, and are accepted within 30 days 32.7% of the time against 84.4% for unassisted PRs |
| GitHub | The bottleneck is reviewing code, not writing it. More than **1 in 5** reviews now involve an agent |

## What Riffle does

Riffle is a GitHub App. When a pull request opens, it scores the change and
sorts it into one of three bands — `review_first`, `standard`, or
`senior_recommended` — with an explanation of what to check first.

Nothing is auto-approved and nothing is hidden. Riffle reorders the queue;
it never merges, and it never removes a PR from review.

**The difference is where the signal comes from.** A change to a config file
might be routine in one codebase and the most common cause of incidents in
another. Generic heuristics cannot tell the difference. Riffle trains a model
**per repository** on that repository's own merge, revert, and follow-up-fix
history.

## Architecture

Four independently deployable services.

| Service | Language | Responsibility |
| --- | --- | --- |
| `intake` | Go | Verify webhook signature, deduplicate by delivery ID, publish to queue, return 200 inside 10 seconds. Nothing else. |
| `scorer` | Python | Consume events, extract features, run the tenant ranking model, call the explainer, write the result |
| `explainer` | Python | LLM inference behind an API. Warm GPU, cached, rate-limited, times out to a template fallback |
| `app` | TypeScript | GitHub App plus the Next.js dashboard. Everything that talks to GitHub or to humans |

Supporting: Postgres (features, tenants, outcomes), Redis (idempotency keys,
inference cache), Pub/Sub (event bus), Airflow (scheduled retraining), MLflow
(registry), Kubernetes Jobs (per-tenant training).

**Each boundary is a place where the failure mode changes.** Intake must never
be slow, because GitHub gives us 10 seconds. Scoring must never be lost,
because an unranked PR is a broken promise. Explanation is allowed to fail,
because a missing sentence degrades quality and not correctness.

> **The LLM is never on the correctness path.**

## Invariants

These are not preferences. Breaking one is a bug even if the tests pass.

- **Idempotent scoring** — the same delivery ID must never produce two scores
- **Version pinning** — a request never mixes a new model with an old feature extractor
- **Tenant fairness** — one monorepo pushing 500 PRs an hour must not starve a small team
- **Training backpressure** — bounded job pool; excess requests queue or defer, never run
- **Fail open, not closed** — if scoring fails, the PR still appears in the queue, unranked and flagged
- **Explanation is optional** — the explainer's failure degrades quality only

## Repository layout

```
riffle/
  services/
    intake/       Go, webhook front door
    scorer/       Python, feature extraction and ranking
    explainer/    Python, LLM inference service
    app/          TypeScript, GitHub App and dashboard
  pipelines/
    ingestion/    dataset download and normalisation
    training/     per-tenant training, evaluation, promotion
    dags/         Airflow DAGs
  contracts/      shared JSON schemas, the source of truth
  infra/
    docker/ k8s/ terraform/
  fixtures/       webhook payloads and a seed tenant
  site/           the marketing page at riffle.dev
  docs/
```

`contracts/` is the important directory. Any change to a schema there is a
breaking change.

Every directory carries a `README.md` describing what belongs in it and which
invariants it owns. Read that before adding code to one.

## Local development

**Prerequisites:** Docker, Go 1.22+, Python 3.11+, Node 20+, and the `gh` CLI.

```bash
git clone git@github.com:ru-dr/riffle.git && cd riffle
cp .env.example .env
make bootstrap && make up && make seed
```

`make up` brings up Postgres, Redis, a Pub/Sub emulator, and all four
services. `make seed` loads a fixture tenant so the dashboard renders.

```bash
make test
make lint
make logs SERVICE=scorer
make down
```

**Webhooks without GitHub:**

```bash
make replay FIXTURE=fixtures/pr_opened.json
gh webhook forward --repo=<org>/<test-repo> --events=pull_request \
  --url=http://localhost:8080/webhook
```

**Training locally** — runs on CPU and should finish in minutes:

```bash
make train    TENANT=fixture
make evaluate TENANT=fixture
make promote  TENANT=fixture VERSION=<version>
```

## Contracts

The event `intake` publishes and `scorer` consumes:

```json
{
  "delivery_id": "string, GitHub X-GitHub-Delivery header",
  "tenant_id":   "string, installation id",
  "repo":        "string, owner/name",
  "pr_number":   0,
  "action":      "opened | synchronize | reopened",
  "head_sha":    "string",
  "received_at": "RFC3339 timestamp"
}
```

The result `scorer` produces and `app` consumes:

```json
{
  "delivery_id":   "string",
  "tenant_id":     "string",
  "pr_number":     0,
  "risk_score":    0.0,
  "rank_band":     "review_first | standard | senior_recommended",
  "model_version": "string, pinned for this request",
  "features":      { "...": "the vector used, for audit" },
  "explanation":   "string or null when the explainer timed out",
  "scored_at":     "RFC3339 timestamp"
}
```

`explanation` is nullable by design. A null means the explainer was slow or
down — the rank is still valid.

## Observability

Metrics are prefixed `riffle_` and always carry a `tenant` label. Logs are
structured JSON and always carry `delivery_id`, which traces one PR across
all four services.

| Metric | Meaning |
| --- | --- |
| `riffle_intake_latency_seconds` | Must stay far under the 10s GitHub budget |
| `riffle_scoring_duration_seconds` | Event received to result written |
| `riffle_explainer_timeouts_total` | How often we degrade to no explanation |
| `riffle_queue_depth` | Drives KEDA autoscaling |
| `riffle_tenant_model_auc` | Per-tenant ranking quality over time |
| `riffle_drift_score` | Feature distribution shift per tenant |

## Data

| Dataset | Use |
| --- | --- |
| [AIDev](https://arxiv.org/html/2601.15195) | Primary training data. Agent-authored PRs with merge outcomes, CI results, reviewer interactions |
| On the Shoulders of Giants | 69 engineered features for PR outcome prediction (MSR 2020) |
| ApacheJIT | 106,674 commits, 28,239 labelled bug-inducing. Bootstraps the defect signal |
| Live ingestion | Labels from merge, revert, and follow-up-fix history on connected repositories |

Raw downloads are never committed. DVC tracks them; pointer files are in git
and the data lives in a bucket.

## Deployment

| Environment | Where | Notes |
| --- | --- | --- |
| `local` | Docker Compose | Emulated Pub/Sub, no GPU, stub explainer by default |
| `staging` | GKE, small pool | Real GitHub App on a test org, real Pub/Sub, one shared GPU node |
| `prod` | GKE | One shared GPU node for the explainer; per-tenant training is CPU only |

## Glossary

| Term | Meaning here |
| --- | --- |
| **Tenant** | One GitHub App installation. Its own model, data, and metrics |
| **Rank band** | The three-way output: review first, standard, senior recommended |
| **Promotion** | Moving a newly trained tenant model into serving after it beats its predecessor on that tenant's holdout |
| **Fallback** | Serving the global base model when a tenant model is missing or underperforms |
| **Drift** | Feature distribution shift for a tenant, measured against the window its current model trained on |

## Status

Riffle is **in development** and not yet ready for production use. The
architecture, contracts, and invariants above are settled; the
implementation is in progress.

## License

[Apache-2.0](LICENSE)
