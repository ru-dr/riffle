# training

Per-tenant training, evaluation, and promotion. Runs as Kubernetes Jobs, CPU
only.

- **Train** on the tenant's own merge/revert/fix history.
- **Evaluate** against that tenant's holdout, not a global benchmark.
- **Promote** only on a win over the current model; otherwise keep serving it.
- **Fall back** to the global base model when a tenant model is missing or
  underperforms.

The job pool is bounded. One tenant retraining must not starve the rest.
Models and metrics are registered in MLflow.
