# Merge-outcome model: writeup

Ananth T A. Riffle model pipeline, November 2026.

## 1. Summary

We predict, when a pull request opens, whether it will land clean, land after rework or go nowhere. We also predict
why it would go nowhere and its post-merge labels (revert, SZZ bug, "bad"). The model is a blend of LightGBM,
XGBoost and logistic regression, on 161 features that are all known when the PR opens.

| | Seen repos (by PR) | New repos (by repo) |
|---|---|---|
| Accuracy, 3 outcomes (random guess 33%, always the commonest 36% / 35%) | **64.4%** | **60.3%** |
| When the model is at least 70% sure (34% / 26% of PRs), it is right | 84.6% | 81.5% |
| AUROC: clean / rework / goes nowhere | 0.84 / 0.79 / 0.86 | 0.80 / 0.74 / 0.82 |
| Skill, `eval_script.py` (95% range down to) | 0.269 (0.232) | 0.215 (0.185) |
| Skill, fair view (exact diffs, reweighted) | 0.296 | 0.237 |
| Calibration error (limit 0.03) | 0.015 | 0.014 |
| Latency, model, p95 per PR (limit 200 ms) | 0.5 ms | 0.4 ms |
| `eval_script.py` checklist | 7 PASS, 2 FAIL (the queue checks: ties, see 9.1), 1 not checkable | 9 PASS, 1 not checkable |

Two versions ship. `seen_repos` is for repos in the training data; `new_repos` is calibrated for a repo the model
has never seen. Both are trained on all 23 repos of `riffle_mined_v0.1`.

What mattered most, in skill (defined in section 5):
- Removing leaks. Columns that know the future were worth +0.12 to +0.15; most of that was one column,
  `author_association`.
- Our own point-in-time history of each author and repo: +0.06 on new repos, +0.03 on seen ones.
- The choice of model: about 0.005.

Deliverables, all in this folder:
- `pipeline.ipynb`: data, features, models, evaluation and scoring. It needs no project code.
- `models/seen_repos/` and `models/new_repos/`: written by the notebook (89 MB, so not in git; shared as a zip)
- `eval/by_pr/` and `eval/by_repo/`: the eval script's reports and charts
- `predictions/`: an in-sample demo of the scorer's output, written by the notebook (not in git)
- this writeup

## 2. The task and its constraints

- Input: the miner's dataset table, one row per closed PR, features as at open. The snapshot and commit tables
  describe PRs after they opened, so a model scoring at open cannot use them.
- Output: at least `sample_output.csv`'s 56 columns. Ours has those 56 first, in order, then four extras.
- The prediction is made when GitHub's webhook fires (the team's option a), so nothing after open may reach the
  model. That rule drove most decisions below.
- The team's evaluation script decides which model is best.

## 3. Rows

| Step | PRs (v0.1) |
|---|---|
| In the table | 192,998 |
| Known outcome | 192,963 |
| Opened 90+ days before the data was collected, so the outcome has settled | 192,539 |
| At-open diff not `final` | 183,658 |
| Outside contributor: not a bot, not a confirmed maintainer before this PR opened. **Scored** | 67,550 |
| At-open diff exact. **Trained on** | 58,942 |

**Why only outside contributors.** Training on outside PRs alone scored better (+0.006) than training on every PR and
scoring the outside ones. It also trains four times faster.

**Why exact diffs, reweighted.** When the miner cannot read the diff exactly as it was at open, it either rebuilds
it (`recovered`, `approx`) or falls back to the final diff (`final`).
- `final` rows are dropped: their diff is the post-rework one.
- `recovered` rows look harmless but are not. They are 0% of clean PRs and 42% of rework PRs, and a model can tell
  them from exact rows at AUROC 0.64 from which columns are blank alone (0.5 would mean no tell).
- In production the diff is always exact, so models train on exact rows only. Each row is weighted by (rows of its
  repo and class) / (exact rows among them), so every repo keeps its true class mix.
- Dropping them *without* reweighting broke calibration (error 0.034 to 0.040) because rework PRs went missing.
  With reweighting: +0.008 skill.

## 4. Leaks

Each of these was found by checking how blanks and values relate to the outcome, or from the miner's documentation.
All were dropped, including ones Nishit's input files keep; he asked us to drop anything that would not be known
when the webhook fires.

| Column or pattern | What is wrong | Skill it added (by PR / by repo) |
|---|---|---|
| `author_association` | GitHub's current status of the author, read at mining time. First-timers whose PR went nowhere stay `NONE` (92% go nowhere); those whose PR later landed become `CONTRIBUTOR` (20%). | +0.117 / +0.122 |
| Body columns (`body_*`, `has_linked_issue`, `agent_boilerplate`) | The body is read at mining time, so it includes edits made after open. | +0.004 / +0.004 |
| `max_sim_open_prs`, `max_sim_recent_merged` | Similarity on that same mining-time body. | 0.000 / +0.001 |
| `ai_agent_authored`, `agent_name` | Partly detected from body watermarks. | +0.001 / 0.000 |
| `author_prs_merged_repo` | Blank only on merged PRs: a blank says "merged". Replaced by our own author history. | +0.003 / +0.001 |
| `recovered` diffs | Blank-column fingerprint, above. | inflated the all-rows view by about 0.009 |
| `head_from_fork` blank | The fork was deleted later: 16% of goes-nowhere PRs, 7% of clean ones. Read as "a fork". | not measured alone |
| PRs with no `closed_at` | 1,339 goes-nowhere PRs: the miner's stale-open rows (still open when mined, labelled abandoned at mining). Our first history code treated them as closed when they opened, so their outcome counted too early. They now stay open in everyone's history and their outcome is never read; a test scrambles their outcomes and checks that nothing changes. | about 0.003 on new repos, less on seen repos (passes 1 to 4 rerun) |

**Checked and not leaks:**
- `EXP`, `REXP`, `SEXP` count commits before the PR's starting point at open. Across one author's PRs, EXP rises
  55% of the time, stays 41%, falls 4%; it is not a single value per author. Their 0.99 correlation is by design.
- `AGE` blanks are PRs that only add new files (99% of them).
- `title_len`, `has_milestone` and `declared_hotfix` are rebuilt as at open.

**Bugs in our own history code, caught before results were used:**
- Timestamps from parquet are microseconds, not nanoseconds, so a "90-day" window was really all-time.
- Unknown authors were pooled under one key.
- Window-less counts did not subtract other keys.

A unit test caught each one, and a cut test guards against peeking: features of PRs opened before a cut date must be
identical when everything after the cut is unknown. The notebook runs it every time.

## 5. How we measured

- **Two splits**, because they answer different questions:
  - *By PR*: 20% of PRs held out. The repos are seen in training. This is the short-term case of the team's repos.
  - *By repo*: 5 folds of whole repos. Each repo is scored by a model that never saw it. This is the worst case
    of a new installation.
- **Skill** is the eval script's headline: 1 − Brier / base-rate Brier, averaged over repos. The base rate gives
  every PR the training outcome mix. **It is not accuracy.** 0.29 means the model removes 29% of the base rate's
  error; section 9 gives accuracy as well.
- **Fair view**: exact-diff rows reweighted to each repo's class mix, as production will see. The eval script scores
  every row, recovered ones included, so its numbers are slightly more conservative.
- **Noise**: seed-to-seed sd of skill was about 0.002 by PR and 0.0005 by repo, so smaller differences mean nothing.

## 6. What we tried

Five passes. Each held everything fixed except the question it asked. The full tables are in `FEATURES_LOG.md`;
the decisions and their causes, in order, are in `DECISIONS.md`.

| Pass | Question | Answer |
|---|---|---|
| EDA | What is in the data; what leaks? | The leaks in section 4. Repos differ widely: goes-nowhere ranges from 11% to 49%, and first-timers go nowhere 86% of the time at drizzle against 20% at rollup. Outcomes drift: goes-nowhere was 16% of PRs opened in 2024 and 26% in 2026. Size separates clean from rework. |
| 1 | Which feature groups help? | Author history +0.03 on both splits. Repo history +0.027 on new repos only. Consolidating redundant columns costs nothing. Open hour and weekday, and author history across repos, add nothing. Training on outside PRs only: +0.006. |
| 2 | Rows, leaks, pruning, more history | Exact rows with reweighting (+0.008). Each leak's value (section 4). No group can be pruned. More history features (last outcomes, recent windows, relative size) add nothing. |
| 3 | Which model? | Boosting wins on its own: LightGBM 0.293 / 0.237, XGBoost level with it, CatBoost behind, networks 0.02 to 0.03 behind. A GRU reading the author's and repo's previous PRs beats a plain network but not the trees. Blends add about 0.005. |
| 4 | Each blend's cost and benefit | Below. |
| 5 | The deliverable | This notebook. |

**Specialise or generalise?** On seen repos:
- one model per repo: 0.273
- one shared model: 0.284
- a shared model that also knows the repo: 0.292

With 23 repos, sharing already beats specialising. The full corpus (43 repos, about 5x the PRs) should favour
sharing more.

**Pass 4, the blend.** Quality over 3 seeds, and cost:

| Setup | By PR | By repo | Train, CPU only | Libraries | 1 PR | Exact explanations |
|---|---|---|---|---|---|---|
| LightGBM | 0.293 | 0.235 | 23 s | 10 MB | 6 ms | yes |
| + XGBoost | 0.295 | 0.236 | 60 s | 94 MB | 21 ms | yes |
| + logistic regression | 0.292 | 0.239 | 37 s | 187 MB | 15 ms | yes |
| **G: + XGBoost + logistic regression (chosen)** | 0.294 | 0.239 | 75 s | 271 MB | 30 ms | yes |
| H: + XGBoost + GRU sequence model | 0.297 | 0.239 | 210 s | 94 MB + torch | 34 ms | trees only |

G was chosen over H. H's +0.002 on seen repos is smaller than many differences we treat as noise. It costs torch
(6.2 GB with CUDA in our environment), a serving path that fetches each author's and repo's recent PRs, and exact
per-PR explanations for part of the score. G is deterministic, needs no GPU, and every part of it is explainable.
XGBoost trains on the CPU because on the GPU the same seed gave probabilities up to 0.17 apart.

## 7. The final model

- **Outcome** (per seed):
  - LightGBM, XGBoost and logistic regression, each early-stopped (the trees) and calibrated by vector scaling on
    validation rows.
  - A geometric blend of their probabilities. Its weights, a temperature and biases are fitted on the same
    validation rows.
  - At scoring time, three seeds are averaged.
- **Why it goes nowhere**: LightGBM, 5 reasons, on goes-nowhere PRs; vector-scaled.
- **Post-merge labels**: LightGBM per label, on merged PRs whose label was observed; heavily regularised and
  Platt-scaled. With too few observed positives, a label falls back to its training base rate.
- **Validation**:
  - `seen_repos`: 15% of PRs.
  - `new_repos`: 15% of whole repos (at least 2), so its calibration reflects repos the model has not seen.

**Output conventions (as `sample_output.csv`):**
- `p_label_merged = p_clean + p_rework`
- `p_label_rework = p_rework / p_label_merged`: rework, given that it merges.
- `p_label_<reason>` and `p_close_reason_<reason>` equal `p_reason_<reason>`: given that it goes nowhere.
  (`unknown` is named `other` in the `close_reason` columns.)
- The eval script's consistency check expects `p_nowhere × p_reason` instead. Its scoring of those columns follows
  `sample_output`, so we kept the conditional form.

**Two definitions of `label_bad`:**
- `p_label_bad` predicts the **team's** definition: revert OR hotfix, on PRs merged 30+ days before the data was
  observed (Nishit's input files, citing ADR 0021). The output's `label_bad` and `label_bad_observed` use the same
  definition.
- `p_label_bad_miner` predicts the **miner's** broader `label_bad` (any post-merge breakage signal, CI included,
  observed on about 12% of merged PRs). It sits next to `label_bad_miner` and `label_bad_miner_observed`.
- They are separate models, so predicting both costs the outcome nothing.

Extra columns after the 56: `p_label_hotfix`, `p_label_bad_miner`, `label_bad_miner`, `label_bad_miner_observed`.

## 8. Using it

```python
out = score(table, "models/new_repos")    # defined in the notebook, section 8
```

- `table` is the miner's dataset table and must contain **every PR of each repo, closed ones included**: the
  history features count the PRs that closed before each PR opened. In production that means a store of past PRs
  per repo, which Riffle would fill when it is installed. This is the main assumption to confirm with the team.
- Use `seen_repos` for repos in the training data and `new_repos` otherwise.
- Latency per PR = the feature step's time / PRs + the model's time per batch of 256 / 256.

Measured on this table (in-sample, 71,885 outside PRs, the `seen_repos` model): median 1.2 ms per PR, p95
1.4 ms, features included. The saved models are 66 MB (`seen_repos`) and 22 MB (`new_repos`). These are three
seeds of each member plus the reason and label models; most of the size is XGBoost.

## 9. Results

### 9.1 The outcome

`eval_script.py` checklist (reports in `eval/by_pr/report.txt` and `eval/by_repo/report.txt`):

| Check | By PR | By repo |
|---|---|---|
| File complete and valid | PASS | PASS |
| Reads no banned columns | PASS | PASS |
| Beats the base rate overall | PASS (0.269, down to 0.232) | PASS (0.215, down to 0.185) |
| Beats the base rate in every main group | PASS | PASS |
| Calibration error at most 0.03, slopes 0.8 to 1.2 | PASS (0.015; 0.99, 0.92, 1.01) | PASS (0.014; 0.99, 1.00, 1.05) |
| Honest within each repo tier | not checkable (no tier column) | not checkable |
| "Likely to go nowhere" list beats PR age only | **FAIL**: −0.003 (range −0.080 to +0.062) | PASS: +0.075 (+0.045 to +0.106) |
| ... beats the simple heuristic | **FAIL**: −0.010 (range −0.082 to +0.046) | PASS: +0.083 (+0.048 to +0.118) |
| 95% of PRs within 200 ms | PASS | PASS |
| 3 or more seeds | PASS | PASS |

**The two by-PR failures are ties, not losses.** The by-PR test holds out 20% of PRs, so the replayed queues contain
only test PRs: 205 moments in 15 repos, where 84% of the waiting PRs went nowhere. Every ordering then finds dying
PRs at about the same rate (model 0.872, heuristic 0.882, PR age 0.875, random 0.757), and the ranges span zero.
- In the same table, the model's review-next ordering is clearly better: top-5 quality 0.693, against 0.615 for PR
  age and 0.646 for the heuristic.
- The by-repo test has full queues and passes both checks by +0.075 and +0.083.

**Where the errors are** (every test row, the most likely outcome taken as the prediction):

| Actual | Predicted clean | rework | nowhere (by PR) | | Predicted clean | rework | nowhere (by repo) |
|---|---|---|---|---|---|---|---|
| clean | **0.71** | 0.18 | 0.11 | | **0.66** | 0.19 | 0.15 |
| rework | 0.27 | **0.55** | 0.19 | | 0.26 | **0.50** | 0.24 |
| nowhere | 0.17 | 0.19 | **0.65** | | 0.18 | 0.16 | **0.66** |

Telling rework from clean is the hardest part: about 27% of rework PRs look clean at open.

### 9.2 Reasons and labels

Skill against the base rate (the eval script's "other labels" and "why PRs go nowhere" sections):

| Output | By PR skill | AUROC | By repo skill | AUROC |
|---|---|---|---|---|
| `p_label_merged` | 0.354 | 0.86 | 0.286 | 0.82 |
| `p_label_rework` (given merged) | 0.276 | 0.80 | 0.237 | 0.78 |
| `p_label_abandoned` (given nowhere) | 0.143 | 0.72 | −0.013 (range −0.057 to +0.025) | 0.61 |
| `p_label_duplicate` | 0.127 | 0.75 | 0.024 | 0.63 |
| `p_label_unwanted` | 0.098 | 0.74 | 0.015 | 0.61 |
| `p_label_szz_bug` | 0.231 | 0.81 | 0.144 | 0.77 |
| `p_label_revert` | 0.009 | 0.71 | −0.001 | 0.44 |
| `p_label_bad` (revert or hotfix) | 0.008 | 0.73 | 0.000 | 0.52 |
| `p_label_hotfix` (extra) | base rate | — | base rate | — |

What the table says:
- Reasons have real skill on seen repos: F1 0.51 averaged over the four reasons.
- On new repos, reasons are close to the base rate even after shrinking. The model mostly says "abandoned" (recall
  0.97), the commonest reason.
- Revert and `label_bad` are rare (0.6% to 0.7% of merged PRs). They rank somewhat on seen repos (AUROC 0.71 to
  0.73) and not at all on new repos; their calibration is fine, so they behave like base rates there.
- SZZ bug is the one post-merge label with skill on both splits.
- Hotfix has too few positives (277 in all) to train on, so it is the base rate.
- `p_label_ci_technical` is badly calibrated against `label_ci_technical` (error 0.14 by PR). The miner fills that
  label only for goes-nowhere PRs where CI was observed, and 38% of those are CI failures. `p_label_ci_technical`
  follows `sample_output`'s convention (equal to `p_reason_ci_technical`, about 8% of all goes-nowhere PRs), so the
  two describe different populations. A model trained on the observed population would fix it.
- `p_label_bad_miner` has no label of that name in the eval script's list, so the script ignores it. Its truth
  columns are in the output for anyone who wants to score it.

## 10. Limitations and open questions

- **Point-in-time has a ceiling.** Much of the outcome is decided after open: whether a reviewer turns up, asks for
  changes, whether the author responds, whether CI breaks. The weak spot is telling rework from clean: about 27% of
  rework PRs look clean at open.
- **The body cannot be used yet.** The miner reads it at mining time. Body columns read as at open (ADR 0010's open
  item) could add a little (they were worth +0.004 as a leak, an upper bound).
- **Data:**
  - 23 repos, one domain (web-ts), one sixth of the planned corpus.
  - 10,495 rows come from 6.x miners, whose `label_revert`, `label_hotfix` and SZZ labels mean slightly different
    things.
  - The weakest repo under every setup is nestjs/nest (by-repo skill about 0.06).
- **Drift.** Goes-nowhere rose from 16% to 26% over 2024 to 2026. A model trained on the past will under-predict it
  in the future unless retrained regularly. A time-split test from before the feature rework (train before July
  2025, test after: skill 0.211, calibration error 0.043, failing) says recalibration on recent data matters; it
  has not been repeated with the final features.
- **Author identity** is the lower-cased commit email. Authors with several emails look like several people.
- **ADR 0021** (the team's `label_bad`) is not on main yet; the definition here follows Nishit's script.
- **Passes 1 to 4 were rerun after the `closed_at` fix in section 4.** Every comparison came out the same; the numbers
  in section 6 are from the rerun.

## 11. Reproducing

```bash
cd pipelines/training/outcome          # this folder; every path in it is relative to here
cp /path/to/riffle_mined_v0.1.zip .    # or set RIFFLE_DATA=/path/to/riffle_mined_v0.1.zip
jupyter nbconvert --to notebook --execute --inplace pipeline.ipynb
```

- The evaluation script is `pipelines/evaluation/eval_script.py` (or `tools/eval_script.py` when present, as in the
  shared zip). `tools/sample_output.csv` is the team's sample the notebook checks the column order against.
- The code behind passes 1 to 5 is in `experiments/` (see its README). Script names in `DECISIONS.md` and
  `FEATURES_LOG.md` refer to files there.
- The features are also a module, `pipelines/training/outcome_features.py` (ADR 0022), with the same columns.
- About 31 minutes on 16 cores; no GPU.
- `RIFFLE_FAST=1` runs a check on 5 repos in about 5 minutes.
- Requirements: numpy, pandas, pyarrow, scipy, scikit-learn, lightgbm, xgboost, and matplotlib for the evaluation
  script. The versions used: Python 3.12, numpy 2.5.4, pandas 3.1.1, pyarrow 26.0.1, scipy 1.18.1, scikit-learn 1.9.1, lightgbm 4.7.0, xgboost 3.4.1, matplotlib 3.11.2.
