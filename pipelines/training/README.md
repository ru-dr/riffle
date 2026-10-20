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

## Loading the miner's tables

`frames.py` is the one place the miner's three tables (dataset, snapshots, commit
features) become training frames. They are joined here at load time, not stored
merged, because they have different grains: one row per PR, per snapshot, per head
commit.

```python
import frames
t = frames.load_tables("dataset_merged.parquet")   # finds _snapshots and _commit_features next to it
f = frames.build_frames(t, by="pr", test_frac=0.2)
f.breakage_train      # merged PRs with an observed label_bad; target label_bad
f.outcome_train       # snapshots; target outcome_3class; weight snapshot_weight
f.inputs(f.outcome_train)   # the columns a model may read
```

It splits by PR (or `by="repo"`) from a stable hash, so a PR's snapshots never
straddle train and test and every frame agrees on the side. It nulls the diff
features on rows whose at-open diff was not recoverable, drops `near_decided`
snapshots, rescales each PR's weights to add up to 1, never offers outcome, id or
metadata columns as inputs (read from the contract), and refuses snapshots that no
longer match their dataset (`check_fresh=False` to skip). Run
`python -m pytest pipelines/training/tests`.
