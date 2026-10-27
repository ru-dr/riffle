# 0014: MLOps tooling stack

- **Date:** 2026-10-20
- **Status:** Proposed
- **Owners:** #5 Data ingestion, #2 Training

## Context

The README fixes the services (`intake`, `scorer`, `explainer`, `app`) and names some supporting tools: Postgres, Redis, Pub/Sub, Airflow, MLflow, Kubernetes Jobs, KEDA, DVC and the `riffle_*` metrics. Other ADRs fix single pieces, such as Airflow on a Compose VM ([ADR 0013](0013-airflow-on-a-compose-vm.md)) and Go for data movement ([ADR 0004](0004-go-for-data-movement.md)). No record says which tool covers data validation, model training, serving, logging, tracing, drift, alerting, infrastructure or secrets, or which popular tools were ruled out. The data is small: ApacheJIT holds 106,674 commits and the miner writes three Parquet tables, so it fits on one machine. Training and scoring are CPU only unless `llm.provider` is `self_hosted`. This ADR records the whole stack in one place so that each later ADR can change one row.

## Decision

### Data

- **Format:** Parquet for all mined and normalised tables, as the miner writes today.
- **Versioning:** DVC with pointer files in git and the data in a bucket, as the README states. No table format layer.
- **Processing:** pandas in `pipelines/training/frames.py`; DuckDB for ad-hoc queries over the Parquet tables. No distributed engine.
- **Validation:** Pandera checks on the joined training frames before training: null rates, value ranges, label leakage. The JSON Schemas in `contracts/` keep ownership of column names and types.
- **Label maturity:** `frames.py` keeps only labels that were mature at the training cut-off. A test covers it.
- **Features:** one code path in `libs/features/`, imported by `scorer` and by `pipelines/`. No feature store.

### Training

- **Global model:** LightGBM. It reads nulls natively, which matches the rule that an unobserved label stays null, and it runs on CPU.
- **Tuning:** Optuna, with time-ordered folds only. No random splits.
- **Per-repo layer:** scikit-learn calibration (isotonic or Platt), fitted per tenant, starting at weight 0 ([ADR 0001](0001-global-first.md)).
- **Explanations:** SHAP TreeExplainer feeding the explainer's templates.
- **Tracking and registry:** MLflow. Each registered model records the model, the layer, the feature extractor version and the rules hash that make up `model_version`.
- **Promotion gate:** a new model or layer is promoted only if it beats its predecessor on a time-ordered holdout (the tenant's own holdout for a layer). Otherwise the tenant falls back to `layer_weight` 0.
- **Backtest:** `pipelines/backtest/` replays time-ordered history and reports effort-aware recall and lift over FIFO.
- **Compute:** Kubernetes Jobs in a bounded pool, CPU only.

### Orchestration and CI

- **Scheduling:** Airflow runs the miner, shard merge, DVC push, validation, retraining, drift checks and backtests as DAGs in `pipelines/dags/`. It launches Kubernetes Jobs with `KubernetesPodOperator` and does no heavy work itself.
- **Events:** Pub/Sub stays the only event bus, with an emulator locally.
- **CI:** GitHub Actions, one job per service, as `CLAUDE.md` already requires. A small backtest runs on changes to `pipelines/training/` or `libs/features/`.

### Serving

- **`intake`:** Go, as in the README.
- **`scorer`:** Python with FastAPI for health and readiness, plus a Pub/Sub consumer. The model loads in-process. No separate model server. ONNX Runtime is allowed later if inference latency needs it.
- **Idempotency and cache:** Redis, keyed by `delivery_id`.
- **Autoscaling:** KEDA on `riffle_queue_depth`.
- **Contract checks:** `jsonschema` against `contracts/` at service boundaries.

### Observability

- **Metrics:** Prometheus format, exported with the `riffle_` prefix and a `tenant` label, as listed in the README. Grafana for dashboards.
- **Pipeline:** every service emits logs, traces and metrics as OpenTelemetry (OTLP) to an OpenTelemetry Collector. The Collector routes them to Loki, Tempo and Prometheus. Services do not know the backends.
- **Logs:** structured JSON with `delivery_id` on every line, sent through the Collector to Grafana Loki. Loki stores chunks in object storage (GCS on GKE, local disk in Compose).
- **Log labels:** only `service`, `env` and `level` are Loki labels. `delivery_id`, `tenant` and `repo` are structured metadata, never labels, because they are high cardinality. Free-text fields are never labels.
- **Log content:** no code, diffs, or author identities in logs. This follows the rule that nothing leaves the installation by default.
- **Retention:** set explicitly in Loki, with a default recorded in `infra/`. The value is open (see Open).
- **Traces:** OpenTelemetry in all four services, stored in Grafana Tempo, with `delivery_id` as an attribute so one PR can be followed end to end. Grafana links a log line to its trace and back.
- **Drift:** `riffle_drift_score` computed as PSI per tenant against the window the current model trained on. Evidently is allowed later if PSI is not enough.
- **Model quality:** `riffle_tenant_model_auc` computed from matured outcomes.
- **Alerting:** Grafana alerts on intake latency near the 10 second GitHub budget, queue depth, explainer timeouts, parser failures, drift above a threshold, and tenant AUC falling below the global model's.
- **Retraining triggers:** a schedule, plus a drift or quality alert that raises a request. Requests go through the bounded pool and queue or defer, never run unbounded.

### Infrastructure and security

- **Environments:** Docker Compose locally, GKE for staging and prod, as in the README.
- **Provisioning:** Terraform for cloud resources, Kustomize for Kubernetes manifests.
- **Secrets:** GCP Secret Manager, surfaced to pods with External Secrets. No secrets in manifests, images or git.
- **Data leaving the installation:** none by default. Any outcome sharing stays opt-in and off.

## Options not taken

| Option | Why not |
| --- | --- |
| Apache Spark | Needs a cluster and is built for far larger data than the current tables |
| Apache Kafka | Pub/Sub already fills the same role, is managed, and has a local emulator |
| Apache Flink or Beam | Stream processing. Scoring handles one event per webhook and has no windowed stream job |
| Apache Iceberg, Delta Lake or Hudi | DVC with Parquet already versions the tables. Concurrent writers and very large tables are not current needs |
| Great Expectations | Heavier than Pandera for the same checks on a few tables |
| Feast or another feature store | One feature code path in `libs/features/` already serves training and scoring |
| Kubeflow, Metaflow or Ray | Airflow plus Kubernetes Jobs already covers scheduling and compute |
| Triton, Seldon or BentoML | A separate model server adds a hop and a failure point. The scorer loads the model in-process |
| XGBoost instead of LightGBM | Comparable accuracy expected, but LightGBM's null handling and speed on CPU fit the data. Not benchmarked |
| Weights & Biases or Neptune | Hosted trackers. Riffle is self-hosted and MLflow is already chosen |
| Vector database and RAG | Nothing in the product retrieves documents |
| Helm instead of Kustomize | More machinery than the number of services needs |
| Google Cloud Logging as the log store | Zero operations on GKE, but it does not exist locally, so local and prod would differ, and logs would not link to traces in Grafana |
| ELK or OpenSearch | Full-text indexing of every line costs more to run than Loki's label-only index, for queries Riffle does not need |

## Consequences

- One machine runs the data phase and training. No cluster to operate for data.
- New dependencies: Pandera, Optuna, FastAPI, OpenTelemetry, and DuckDB for queries. `pipelines` and `scorer` CI jobs install the ones they use.
- Loki, Tempo and an OpenTelemetry Collector are added to Compose and to the GKE manifests. The team runs them, which is an operations cost that Cloud Logging would not have had.
- Every service needs an OTLP exporter and a shared JSON log format with `delivery_id`. A log schema and the label rules belong in `contracts/` or `infra/`.
- `pipelines/dags/` gains DAGs for shard merge, DVC push, validation, retraining and drift checks.
- `pipelines/backtest/` and `libs/features/` become required before the first scored PR, since they back the promotion gate and the train/serve match.
- The outcome watcher ([ADR 0004](0004-go-for-data-movement.md), item 3) becomes a dependency of the per-repo layer, so it should not be treated as a stretch item.
- Invariants are kept. The LLM stays off the scoring path, scoring stays idempotent and point-in-time, and the label maturity test strengthens the no-leakage rule. If the Go miner replaces the Python miner, it must write Parquet that passes the same checks.

## Revisit if

- A single table no longer fits in memory on the training machine, or one training run takes longer than the retraining schedule allows.
- More than one process needs to write the same table at the same time.
- Outcome ingestion needs windowed or high-volume stream processing that Pub/Sub consumers cannot handle.
- Scoring latency at the 95th percentile cannot meet the budget with in-process LightGBM.
- LightGBM loses to another model family on the time-ordered backtest.
- PSI misses drift that later shows up as a drop in tenant AUC.
- The team adds a second cloud, or moves off GKE.
- Running Loki and Tempo costs more engineer time than the team can spare, in which case Cloud Logging and Cloud Trace on GKE become the fallback.
- Loki queries on `delivery_id` metadata are too slow at real volume.

## Open

- Current size on disk of each mined table and of the ApacheJIT data. The revisit thresholds need real numbers.
- Whether Prometheus runs as a managed service on GKE or is self-run.
- Log and trace retention periods, and Loki and Tempo sizing. Needs real log volume from staging.
- Whether `tenant` should become a Loki label if the tenant count stays small.
- Alert thresholds and who is paged.
- Whether the per-repo layer is calibration only or also a small residual model.
- Whether the outcome watcher is built before or after the per-repo layer.
- Whether this ADR should be split into one ADR per area before it is accepted, since the ADR rules ask for one decision per file.
