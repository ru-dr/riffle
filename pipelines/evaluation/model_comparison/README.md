# Model comparison: the team's four outcome pipelines on the same PRs

This folder holds the comparison behind the choice of setup G as Riffle's at-open outcome model: four pipelines,
scored by the same evaluation script on the same PRs, with the same truth labels. The numbers are in
`comparison.md`; `paired.csv`, `per_repo.csv` and `metrics_side_by_side.csv` hold every figure in it.

## How it was run

- **Data**: the v0.1 release (`dataset_merged.parquet`, 23 repos), outside-contributor PRs at least 180 days old
  with an at-open diff: 64,788 PRs. Setup G scores PRs from 90 days, so the 2,762 PRs only it has are left out.
- **Split**: Ananth's 5 by-repo folds (`fold_predictions.ananth_fold`): every repo is predicted by models that never
  saw it, and the same repos are held out together for every pipeline.
- **Leaks**: every pipeline is run without the features found to leak the outcome: the PR body and the similarity
  features built from it (read as they are at mining time, not at open), `author_association` and the repo signals
  built from it, `ai_agent_authored`, `agent_name` and `author_prs_merged_repo`. The three pipelines built on the
  shared training table (Nishit, Preeyal, Rudra) also drop the semgrep columns, whose blanks were found to track the
  outcome; setup G keeps `semgrep_findings_new` (its scanner ablation kept them as real signal once recovered diffs
  were left out), so this one input differs.
- **Truth**: one set of labels for all (`--truth-from`, default the first run), so the evaluations differ only in the
  predictions. Where the files disagree on a label (reason cleaning differs between pipelines), `comparison.md`
  says how much.
- **Scoring**: `pipelines/evaluation/eval_script.py` on each file, with the base rate taken from the compared PRs;
  then paired differences between every pair of pipelines on the same PRs, with 95% ranges from resampling whole
  repos 2,000 times.

## The four pipelines

| | Setup G (Ananth) | Nishit v3 | Preeyal | Rudra |
|---|---|---|---|---|
| Code | `pipelines/training/outcome/` | `pipelines/training/` (`prepare_training_data.py`, `fold_predictions.py`) | `pipelines/training/preeyal_riffle_model/training/by_repo_v2.py` | `pipelines/training/rudra_riffle_model/code/by_repo_v3.py` |
| Outcome model | blend of LightGBM, XGBoost and logistic regression, each calibrated (vector scaling), blend weights fitted on held-out rows | LightGBM, Optuna-tuned | LightGBM, then a temperature and per-class shifts ("honesty dial") fitted on the newest PRs | hierarchical LightGBM heads: merge, then rework given merge |
| History features | author and repo history over every mined PR (`ah_*`, `rh*`) | the same `ah_*`/`rh*` (ported from G) plus `hist_*` and `rhist_*` | `hist_*` and `rhist_*` | none |
| Training rows | exact at-open diffs, reweighted to each repo's outcome mix | exact at-open diffs, reweighted | v2 table (recovered diffs included) | exact diffs (the v3 table's weights) |
| Why model | LightGBM, shrunk to the base rate for new repos | LightGBM on cleaned reasons | LightGBM with the dial | LightGBM on `close_reason` |
| Seeds | 3 | 3 | 3 | 3 |

Preeyal's run uses the earlier v2 table, which still had `ai_agent_authored` and `author_prs_merged_repo` and trained
on recovered diffs; the other three use the stricter table.

## Reproducing it

```powershell
# predictions on the shared folds (each writes predictions.csv in the eval_script.py layout)
python pipelines/training/prepare_training_data.py --since 2000-01-01                 # the leak-free table
python pipelines/training/fold_predictions.py --seeds 3 --out eval_results\compare\nishit_v3_by_repo
python pipelines/training/preeyal_riffle_model/training/by_repo_v2.py
python pipelines/training/rudra_riffle_model/code/by_repo_v3.py
#   setup G: run pipelines/training/outcome/pipeline.ipynb; it writes eval/by_repo/predictions.csv

# the comparison
python pipelines/evaluation/compare_runs.py `
  --run setup_G=pipelines\training\outcome\eval\by_repo\predictions.csv `
  --run nishit_v3=eval_results\compare\nishit_v3_by_repo\predictions.csv `
  --run preeyal_v2=eval_results\compare\preeyal_v2_by_repo\predictions.csv `
  --run rudra_v3=eval_results\compare\rudra_v3_by_repo\predictions.csv `
  --truth-from nishit_v3 --out eval_results\compare\report_final
```

`compare_runs.py` also writes `comparison.html`, a self-contained report with every chart of the evaluation script
side by side for the four models; it is large (the charts are embedded), so it stays in `eval_results/` and is not
committed.
