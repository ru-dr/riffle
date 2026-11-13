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

## Slice report

`slices.py` shows label balance per slice of the dataset table, to find groups the model would learn badly
(author association, bot or AI-agent author, domain, repo, PR size quartile). Per slice it gives rows, merged
rate, observed `label_bad` rows, the bad rate over those rows only (null when none are observed) and label
coverage, and flags slices that are `small` or `thin_labels`. It never counts an unobserved label as not bad.

```bash
python pipelines/training/slices.py dataset_merged.parquet --out slices.csv [--strict]
```

`--strict` exits 1 when a slice is flagged. Slice columns and thresholds are arguments, not repository rules.

## Features for the at-open outcome model

`outcome_features.py` turns the miner's dataset table into the inputs of a model that predicts `outcome_3class`
(lands clean, lands after rework, goes nowhere) when a PR opens: 161 columns, 131 from the miner and 30 engineered
(ADR 0022).

```python
import outcome_features as OF
d = OF.build_features(dataset)       # the whole table: history needs every earlier PR of each repo
X = d[list(OF.FEATURES)]
rows = OF.training_rows(d)           # rows.scored, rows.train (exact at-open diffs), rows.weight
w = rows.weight * OF.repo_weights(d["repo"])
```

- **Engineered:** the author's history in the repo (`ah_*`: outcome counts and smoothed rates, PRs open now, first
  PR, days since the previous PR, tenure), the repo's recent behaviour (`rh*`: 90-day outcome mix, PRs open now,
  PRs opened in 30 days, time to close, how outside authors and first-timers fared over 180 days), and consolidated
  columns (`c_*`), among them the 18 LLM detail answers compressed into `c_llm_ran` and `c_llm_flags`.
- **Point-in-time:** a past PR counts only if it closed strictly before this PR opened. A stale-open row (no
  `closed_at`) never lends its outcome. Unknown authors get no author history.
- **Leaks:** `OF.LEAKS` lists the columns a model must not read, with the reason for each.
- **Rows:** score on outside contributors (not a bot, not a confirmed maintainer) whose PR has a known outcome,
  opened at least 90 days before the data was observed, with an at-open diff that is not `final`. Train on those
  with an exact diff, reweighted so each repo keeps its class mix.

Run `python -m pytest pipelines/training/tests/test_outcome_features.py`: the cut test, the stale-open test, and a
check that the column lists still match `contracts/mined_dataset.schema.json`.
