# scorer (Python)

Ranks each live PR with the outcome model (setup G, ADR 0023). It reads `PrFeatures`
(`contracts/pr_features.schema.json`): the PR as a row of the miner's dataset table, made by the
pr-miner with the miner's own code (ADR 0027). It adds the repo's earlier rows from the history store,
builds the model's 161 inputs with the training code, scores, ranks, stores the result in
`score_results` and publishes `ScoreResult` and `ScoreLog`.

```
pr-features ─► scorer ─┬─ history store (gs://…snapshots/history/…)  the repo's earlier PRs
                       ├─ model in memory  ◄── MLflow riffle-outcome-setup-g@champion, checked every minute
                       ├─ score_results (Postgres, key delivery_id)
                       └─► score-results, score-log
```

Invariants:
- **Same model code and inputs as training.** The 30 history features come from
  `pipelines/training/outcome_features.py`, the file the training image uses. The model-running
  functions in `scorer/predict.py` are the training notebook's, copied because the notebook source
  is a template that cannot be imported. The model libraries have the training image's versions.
  `tests/test_same_as_training.py` fails if either one drifts.
- **Idempotent per `delivery_id`.** The row is stored before it is published. A redelivered message
  republishes the stored row and is never scored again.
- **One pinned model per request.** A request keeps the model it started with, and a new champion is
  swapped in whole. `model_version` is `riffle-outcome-setup-g/<version>/new_repos`, and the row's
  `miner_version` is stored with it.
- **Point in time.** Only PRs that closed before this PR opened count in its history
  (`build_features`).
- **Fail open.** A PR is always stored and published: ranked, or unranked with a reason.
- **The LLM is never on this path.** `explanation` stays null until the explainer is built.
- **Fair shares.** At most `TENANT_MAX_IN_FLIGHT` messages of one tenant are scored at once. A tenant
  over its share has its message redelivered after the subscription's backoff.

## What happens to a message

| Case | Result |
| --- | --- |
| An outside contributor's PR, with a model loaded | Ranked: `rank_score`, `rank_band`, the probabilities, and the 161 inputs in the score log |
| A bot or a confirmed maintainer | Unranked, `not_outside_contributor`. The model is trained and evaluated on outside contributors only |
| No model yet (none uploaded, or MLflow never answered) | Unranked, `model_unavailable` |
| The row breaks its contract, or comes from a miner MAJOR version other than the model's `miner_version` tag | Unranked, `invalid_input` |
| Scoring raises (for example, the history store cannot be read) | Retried. On the 3rd delivery: unranked, `scoring_failed` |
| Postgres down, or `score-results` cannot be published | Not acknowledged, redelivered. After 5 deliveries the message goes to the dead-letter topic and is kept for 7 days |
| `score-log` cannot be published | The score still goes out. The log row is dropped and counted (`riffle_score_log_dropped_total`): logging is never on the scoring path (ADR 0024) |
| Not JSON, or no ids | Dropped and logged |

**Ranking (merge_first):** `rank_score = p_lands_clean + 0.6 × p_lands_after_rework`.
**Bands** are cut from the latest score of each other PR of the repo in the last 90 days, using the
`org_config` defaults: top 5% `senior_recommended`, top 20% `review_first`, the rest `standard`.
With fewer than 20 such scores the band is `standard`. Per-tenant settings come with the config
compiler; until then every tenant gets the defaults.

## Uploading the model

The model is not in the image. The scorer downloads the version that the alias `champion` points
to, through the MLflow server. `register_model.py` uploads both folders (`seen_repos`, `new_repos`)
after the gates pass, and moves `champion`:

```bash
kubectl -n riffle-ml port-forward svc/mlflow 5000:5000 &
cd pipelines/training/outcome
python register_model.py --tracking-uri http://localhost:5000 --note "first upload"
```

Then set the tag `miner_version` on that model version in the MLflow UI (Models >
riffle-outcome-setup-g > the version). Use the miner version the training data was mined with, for
example `7.2.2`. The scorer then refuses rows from another miner MAJOR version. Without the tag,
the version is recorded but not checked. Within a minute the scorer logs `model in service` and
`riffle_scorer_model_info` shows the new version. `python register_model.py --rollback` points
`champion` back to the previous version.

## Configuration

In the cluster every value comes from `riffle-platform-settings` (made by Terraform), the chart, or
the Application (`infra/k8s/apps/staging/21-scorer.yaml`).

| Variable | Source in the cluster | Default |
| --- | --- | --- |
| `PUBSUB_PROJECT_ID` | settings | required |
| `SUBSCRIPTION_SCORER_PR_FEATURES` | settings (`riffle-staging-scorer-pr-features`) | required |
| `TOPIC_SCORE_RESULTS`, `TOPIC_SCORE_LOG` | settings | required |
| `BUCKET_SNAPSHOTS` | settings (the history store) | required |
| `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_NAME` | the chart (`cloudSql.enabled`: Cloud SQL Proxy sidecar, IAM login) and settings | `127.0.0.1`, `5432`, required, required |
| `MLFLOW_TRACKING_URI` | Application | `http://mlflow.riffle-ml.svc:5000` |
| `MODEL_NAME`, `MODEL_ALIAS`, `MODEL_VARIANT` | Application | `riffle-outcome-setup-g`, `champion`, `new_repos` |
| `MODEL_CHECK_SECONDS` | — | `60` |
| `MODEL_FIRST_WAIT_SECONDS` | — | `300` (how long the first model check may take before messages are pulled) |
| `WORKERS`, `TENANT_MAX_IN_FLIGHT` | Application | `16`, `WORKERS / 4` |
| `HISTORY_CACHE_SECONDS` | — | `300` |
| `HEALTH_PORT`, `METRICS_PORT` | — | `8080`, `9090` |

Google credentials come from Workload Identity: the pod runs as the `scorer` service account. It may
read the `pr-features` subscription, publish `score-results` and `score-log`, read the `snapshots`
bucket, use the tenant key and log in to Cloud SQL. It has no GitHub key and no access to the models
bucket.

Ports: `8080` serves `GET /healthz` and `GET /readyz`. `/readyz` turns ready once the first model
check is done and messages are being pulled. `9090` (named `metrics`) serves `GET /metrics`:
- `riffle_scorer_messages_total{tenant,result}`
- `riffle_scorer_latency_seconds{result}`
- `riffle_scorer_model_info{model_version,miner_version}`
- `riffle_scorer_fair_share_deferred_total{tenant}`
- `riffle_score_log_dropped_total` (score log rows not published, ADR 0024)

Logs are JSON lines on stdout, with `delivery_id` and `tenant_id`.

## Build, test, deploy

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest tests -q          # the Postgres tests skip without SCORER_TEST_DATABASE_URL (see tests/test_store_postgres.py)
RIFFLE_MODEL_DIR=../../pipelines/training/outcome/models python -m pytest tests -q   # also scores the fixture with the trained model
```

| Step | Where |
| --- | --- |
| Tests | CI job `scorer`, with a Postgres service for the store tests |
| Image | `.github/workflows/images.yml` builds `services/scorer/Dockerfile` from the repo root. On `main` it pushes `…/scorer:sha-<commit>` |
| Deploy | `infra/k8s/apps/staging/21-scorer.yaml`, with the shared chart. Put the new tag in `image.tag`. KEDA scales it on the `scorer-pr-features` backlog |
| Test without the pr-miner | Publish the fixture to `pr-features` (below) |

The installation must have a row in `tenants` (`score_results.tenant_id` references it). The tenant
workflow will make it; until that is built, add the row as a database admin. Then:

```bash
gcloud pubsub topics publish riffle-staging-pr-features \
  --message="$(jq -c '.delivery_id = "test-'"$(date +%s)"'"' fixtures/pr_features/pull_request.opened.json)" \
  --ordering-key="48213/acme-corp/payments#42" \
  --attribute=tenant_id=48213,schema=pr_features/1.0
kubectl -n riffle-serving logs deploy/scorer -c scorer | grep '"scored"'
# → {"msg": "scored", "result": "ranked", "model_version": "riffle-outcome-setup-g/1/new_repos", ...}
```

| File | |
| --- | --- |
| `scorer/__main__.py` | The process: model checks, the Pub/Sub consumer with fair shares, health and metrics |
| `scorer/scoring.py` | One message: checks, fail open, store then publish, the outgoing messages |
| `scorer/predict.py` | The training notebook's model code, and scoring one PR with its history |
| `scorer/models.py` | The champion from MLflow, in memory, swapped whole |
| `scorer/history.py` | The repo's earlier rows from the snapshots bucket |
| `scorer/ranking.py` | `rank_score` and bands |
| `scorer/store.py` | `score_results`, with row-level security |
| `tests/tiny_bundle.py` | A tiny model in the exact saved format, for the tests |

## Not done yet

- **The history store has no writer.** The backfill (the miner run at install) and the outcome
  labeller are not built. Until they are, every repo scores as if it had no history. The files are
  not yet encrypted with the tenant's key (ADR 0025).
- **Only the global model (`new_repos`) is served.** `layer_weight` is 0. The repo model comes later,
  for premium.
- **The `holdout_fraction` of `org_config`** (PRs left unranked to measure lift) is not applied.
- **No per-tenant settings** yet: the config compiler is not built.
- **The Pub/Sub wiring** (subscribe, ack, publish) is covered by unit tests with fakes, not by a run
  against Pub/Sub. MLflow loading was checked against a real MLflow 3.18 server, and the store
  against a real Postgres 17 with the migrations.
