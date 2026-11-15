# Outcome model (setup G)

Predicts, when a pull request opens, whether it will land clean, land after rework or go nowhere, plus the reason
and post-merge label probabilities in `sample_output.csv`'s 56 columns. Start with `WRITEUP.md`.

Every path below is relative to this folder.

| Path | What it is |
|---|---|
| `WRITEUP.md` | The writeup: rows, leaks, how we measured, the five passes, the model, results, limitations |
| `pipeline.ipynb` | The whole pipeline, standalone, executed (outputs included): data, features, leakage tests, training, both evaluations, saving, scoring |
| `eval/by_pr/`, `eval/by_repo/` | `eval_script.py` reports and charts for the two splits (`report.txt` first) |
| `bias_report.py` | Slices the held-out predictions (newcomers, forks, agent branches, size, miner version, repo), flags disparities, writes `eval/*/bias/slices.csv` and `slices.png`; run it after the notebook |
| `tools/sample_output.csv` | The team's sample output; the notebook checks its column order against it |
| `DECISIONS.md` | Every decision in order, with the evidence and what it caused |
| `FEATURES_LOG.md` | Every feature built and tested, pass by pass, with full result tables |
| `experiments/` | The code, results and logs behind passes 1 to 5 (see its README) |
| `models/`, `predictions/` | Written by the notebook; not in git (the trained models are 89 MB) |

The features are also a module, `../outcome_features.py` (ADR 0022), for other models to use. The notebook keeps
its own copy on purpose so that it runs without any project code; both produce the same columns.

## Run it

```bash
cd pipelines/training/outcome
cp /path/to/riffle_mined_v0.1.zip .        # or set RIFFLE_DATA=/path/to/riffle_mined_v0.1.zip
jupyter nbconvert --to notebook --execute --inplace pipeline.ipynb
```

About 30 minutes on 16 cores, no GPU; `RIFFLE_FAST=1` runs a check on 5 repos in about 5 minutes. It uses
`pipelines/evaluation/eval_script.py` (or `tools/eval_script.py` when present). It writes `models/seen_repos/`,
`models/new_repos/`, `eval/` and `predictions/`.

## Score new PRs

After running the notebook, `score(table, "models/new_repos")` (or `models/seen_repos`) takes a raw miner table and
returns the 56 columns in order, then four extras. History features need every earlier PR of each repo in the table.

## Tests

`python -m pytest pipelines/training/outcome/experiments` (from the repo root): the cut test and the stale-open test
for the features. The notebook runs the same checks every time it runs. CI runs these in the `pipelines` job.
