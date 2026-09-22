# pipelines

Offline work: getting data in, turning it into models, deciding whether a new
model is allowed to serve. Nothing here is on the request path.

| Directory | Responsibility |
| --- | --- |
| [`ingestion`](ingestion) | Dataset download and normalisation |
| [`training`](training) | Per-tenant training, evaluation, promotion |
| [`dags`](dags) | Airflow DAGs that schedule the above |

Constraints that live here: **training backpressure** — the job pool is
bounded, and excess requests queue or defer, never run. **Promotion** — a new
tenant model serves only after it beats its predecessor on that tenant's
holdout.

Raw downloads are never committed. DVC tracks them; pointer files go in git
and the data lives in a bucket.
