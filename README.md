<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset=".github/assets/logo-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset=".github/assets/logo-light.svg">
  <img src=".github/assets/logo-light.svg" alt="Riffle" width="300">
</picture>

### Every repo breaks differently.

Riffle orders a team's pull request queue by risk — one model that works from
the first PR, sharpened by what actually broke in your repository. Self-hosted,
open source, and nothing skips review.

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
another. Generic heuristics cannot tell the difference. Riffle serves every
repository with one **global model** from its first pull request, and adds a
**per-repo layer** trained on that repository's own reverts, post-merge CI
failures, and hotfixes. The layer starts at zero weight and gains influence
as labelled outcomes accumulate ([ADR 0001](docs/adr/0001-global-first.md)).

## Architecture

Four independently deployable services.

| Service | Language | Responsibility |
| --- | --- | --- |
| `intake` | Go | Verify webhook signature, deduplicate by delivery ID, publish to queue, return 200 inside 10 seconds. Nothing else. |
| `scorer` | Python | Consume events, read the tenant's compiled config and rules, extract features, run the global model with the tenant's per-repo layer, call the explainer, write the result. Two stages: diff, history, and rules first so the route posts fast; parsers and scanners (lizard, tree-sitter, Semgrep, OSV) second, followed by one rescore. Parsers live in `libs/features/` ([ADR 0005](docs/adr/0005-feature-parsers.md)) |
| `explainer` | Python | Turns the score and its evidence into what to check first. SHAP template explanations by default (`llm.provider: none`); an optional LLM, self-hosted or hosted, phrases them. Times out to the template |
| `app` | TypeScript | GitHub App plus the Next.js dashboard. Everything that talks to GitHub or to humans |

Supporting: Postgres (features, tenants, outcomes), Redis (idempotency keys,
inference cache), Pub/Sub (event bus), Airflow (scheduled retraining), MLflow
(registry), Kubernetes Jobs (global retraining and per-repo layer fits).

**Each boundary is a place where the failure mode changes.** Intake must never
be slow, because GitHub gives us 10 seconds. Scoring must never be lost,
because an unranked PR is a broken promise. Explanation is allowed to fail,
because a missing sentence degrades quality and not correctness.

> **The LLM is never on the correctness path.**

## Invariants

These are not preferences. Breaking one is a bug even if the tests pass.

- **Idempotent scoring** — the same delivery ID must never produce two scores
- **Version pinning** — one `model_version` per request covers the global model, the per-repo layer, the feature extractor, and the compiled rules hash; a request never mixes them
- **Point-in-time scoring** — features, mined history, and the backtest use only data from before the PR opened, and only labels already mature at that moment
- **Tenant fairness** — one monorepo pushing 500 PRs an hour must not starve a small team
- **Training backpressure** — bounded job pool; excess requests queue or defer, never run
- **Fail open, not closed** — if scoring fails, the PR still appears in the queue, unranked and flagged
- **Explanation is optional** — the explainer's failure degrades quality only
- **Nothing leaves by default** — outcome sharing is opt-in and off; when on, only anonymised feature vectors and outcomes leave, never code, diffs, or author identities

## Repository layout

```
riffle/
  services/
    intake/       Go, webhook front door
    scorer/       Python, feature extraction and ranking
    explainer/    Python, template and optional LLM explanations
    app/          TypeScript, GitHub App and dashboard
  pipelines/
    ingestion/    dataset download and normalisation
    training/     global model, per-repo layer fits, evaluation, promotion
    backtest/     time-ordered replay against historical repos
    dags/         Airflow DAGs
  libs/
    features/     Python parsers and feature code, shared by scorer and pipelines
  contracts/      shared JSON schemas, the source of truth
  infra/
    docker/ k8s/ terraform/
  fixtures/       webhook payloads and a seed tenant
    config/       example riffle.yml files
  site/           the marketing page at rifffle.vercel.app
  docs/
    handbook.md
    adr/          one file per decision, numbered
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

**Training locally** — CPU only. Global training should finish in minutes and
a per-repo layer fit in seconds:

```bash
make train-global
make fit-layer TENANT=fixture
make evaluate  TENANT=fixture
make promote   TENANT=fixture VERSION=<version>
make backtest  REPO=<apache-project>
```

Report backtest results as effort-aware recall and lift over FIFO, from
time-ordered validation only.

## Contracts

Every schema, rule kind, setting, and default is specified in
[`contracts/README.md`](contracts/README.md) and
[`contracts/REFERENCE.md`](contracts/REFERENCE.md). The two wire formats below
are the ones every service touches.

The event `intake` publishes and `scorer` consumes:

```json
{
  "delivery_id": "string, GitHub X-GitHub-Delivery header",
  "tenant_id":   "string, installation id",
  "repo":        "string, owner/name",
  "pr_number":   0,
  "action":      "opened | synchronize | reopened | ready_for_review | edited | labeled | ci_completed",
  "head_sha":    "string",
  "received_at": "RFC3339 timestamp"
}
```

`action` covers every trigger `rescore.on` can enable. `ci_completed` is
mapped by `intake` from the check suite event rather than a pull request
action.

The result `scorer` produces and `app` consumes:

```json
{
  "delivery_id":   "string",
  "tenant_id":     "string",
  "pr_number":     0,
  "risk_score":    0.0,
  "rank_band":     "review_first | standard | senior_recommended",
  "model_version": "string, global model + layer + extractor + rules hash, pinned for this request",
  "layer_weight":  0.0,
  "features":      { "...": "the vector used, for audit" },
  "explanation":   "string or null when the explainer timed out",
  "scored_at":     "RFC3339 timestamp"
}
```

`explanation` is nullable by design. A null means the explainer was slow or
down — the rank is still valid.

`layer_weight` is the per-repo layer's share of the score, from 0.0 to 1.0.
It is 0.0 for a new repository, or one whose layer has fallen back, so the
score is the global model's alone.

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
| `riffle_tenant_layer_weight` | How much each per-repo layer contributes; zero means fallback |
| `riffle_drift_score` | Feature distribution shift per tenant |
| `riffle_training_jobs_active` | Running training jobs, against the bounded pool size |
| `riffle_parser_failures_total` | Parser errors and timeouts, by parser; their features become `null` |

## Data

| Dataset | Use |
| --- | --- |
| [AIDev](https://arxiv.org/html/2601.15195) | Primary training data. Agent-authored PRs with merge outcomes, CI results, reviewer interactions |
| On the Shoulders of Giants | Engineered feature set for PR outcome prediction (MSR 2020) |
| ApacheJIT | 106,674 commits, 28,239 labelled bug-inducing. Bootstraps the defect signal and supplies the backtest repositories |
| Live ingestion | Labels from reverts, post-merge CI failures, and hotfixes on connected repositories ([ADR 0002](docs/adr/0002-label-target.md)). SZZ and follow-up fixes are mined and reported as ablations only |

Raw downloads are never committed. DVC tracks them; pointer files are in git
and the data lives in a bucket.

## Deployment

| Environment | Where | Notes |
| --- | --- | --- |
| `local` | Docker Compose | Emulated Pub/Sub, no GPU, template explainer by default |
| `staging` | GKE, small pool | Real GitHub App on a test org, real Pub/Sub. A GPU node only when testing a self-hosted LLM, scaled to zero outside working hours |
| `prod` | GKE | The Expo demo runs here. Global retraining and per-repo layer fits are CPU only; a shared GPU node is added only if `llm.provider` is `self_hosted` |

## Glossary

| Term | Meaning here |
| --- | --- |
| **Tenant** | One GitHub App installation. Shares the global model; has its own per-repo layer, data, and metrics |
| **Global model** | The tree model trained on published datasets and opt-in shared outcomes. Serves every tenant from the first PR |
| **Per-repo layer** | Calibration and adjustments fitted to one tenant's outcomes, applied on top of the global model |
| **Rank band** | The three-way output: review first, standard, senior recommended |
| **Promotion** | Moving a new global model or per-repo layer into serving after it beats its predecessor on holdout — the tenant's own holdout for a layer |
| **Fallback** | Setting a tenant's `layer_weight` to 0, so the global model serves alone, when the layer is missing or makes ranking worse |
| **Drift** | Feature distribution shift for a tenant, measured against the window its current model trained on |

## Contributing

Read [ADR 0003](docs/adr/0003-branch-and-merge-rules.md) and [`docs/adr/PROMPT.md`](docs/adr/PROMPT.md)
before opening a pull request; both are required. Anything argued about for
more than ten minutes becomes a numbered record in `docs/adr/`. Day-to-day
conventions live in [`docs/handbook.md`](docs/handbook.md).

## Status

Riffle is **in development** and not yet ready for production use. The
architecture and invariants above are settled, the contracts are in
review, and the implementation is in progress.

## License

[Apache-2.0](LICENSE)
