# Outcome model: decision log

What we decided, why, and the evidence, in the order it happened. The sequence and what led to what matter, not the
dates. Feature experiments, pass by pass, are in FEATURES_LOG.md.

## Task and constraints (from the team, 2026-11-03 onwards)

| Decision | Why / source |
|---|---|
| Predict `outcome_3class` (lands_clean / lands_after_rework / goes_nowhere) from the PR **at open** | Nishit's answer (option a): the score is made when GitHub's webhook fires |
| Output at least `sample_output.csv`'s 56 columns (`p_clean/rework/nowhere`, `p_reason_*`, `p_close_reason_*`, `p_label_*`, `latency_ms`) | Nishit: "make sure its >= sample_output" |
| Training data is the miner zip `riffle_mined_v0.1.zip`; split train/test on it ourselves | Same data for everyone; the user's instruction |
| Our own feature engineering, not Nishit's `mined_to_input.py` | The user: "not influenced by what nishit made" |
| Drop any column that knows the future, even if Nishit's input keeps it | Nishit: "If you feel any feature is making future leak ... feel free to drop those columns" |
| Latency = input to output, divided by batch size | The user |
| GPU training allowed; work locally, no branch or PR (Nishit pushes the winner) | The user and Nishit |
| Deliverable: a standalone pipeline notebook, the models and a writeup | The user. The notebook imports none of our scripts; the scripts are for testing |
| Deadlines: best model to the team by 2026-11-12, pipeline by 2026-11-15 | Handoff |

## Evaluation design

| Decision | Why |
|---|---|
| Two splits, a final model for each: **by PR** (repos seen in training) and **by repo** (repos never seen) | By PR pays off short term (the team's repos); by repo measures the worst case of a new installation. The user: "two pronged" |
| Also compare **specialisation vs generalisation**: one model per repo, one shared model with the repo as a feature, one shared model without it | The user: does today's data (23 repos) already show a benefit from generalising, or do specialised models still win? The final training set will be 4-5x larger, so the answer may change |
| By-repo validation: 5 folds by repo hash; inner validation by repo for early stopping and calibration | A model tuned on PRs of the same repos looks better than it will be on a new repo |
| Score with `eval_script.py`; report skill against the base rate, calibration error and slopes, the nowhere-list checks, latency, 3 seeds | It is the team's referee |

## Leaks found and how they are handled

| Column or pattern | Evidence | Handling |
|---|---|---|
| `author_association` | Today's status, not at open: first-timers now NONE went nowhere 92%, now CONTRIBUTOR 20% | Drop |
| Body columns (`body_*`, `has_linked_issue`, `agent_boilerplate`) | Read at mining time (miner ARCHITECTURE.md) | Drop |
| `max_sim_open_prs`, `max_sim_recent_merged` | Jaccard on title at open **plus body at mining time** (ARCHITECTURE.md line 522); found 2026-11-10 | Drop (or rebuild from title only, if we can) |
| `ai_agent_authored`, `agent_name` | Partly from body boilerplate; found 2026-11-10 | To test: keep only the non-body signals (`head_branch_agent_prefix`) |
| `post_open_*`, `changes_requested_reviews` | After open | Never inputs (contract) |
| `open_snapshot = final` rows | Diff is the final one; never occurs for clean PRs; 125 diff columns and `base_behind_by` blank exactly there | Drop from training |
| `open_snapshot = recovered` rows | 0% of clean, 37.5% of rework, 12.6% of nowhere. Fingerprint test (`fe_fingerprint.py`): among PRs of one outcome, a model tells recovered/approx from exact at AUROC 0.64 from **blanks alone** (0.67 with values). Blank rates differ by up to 22 points (`min_days_since_last_revert`, the CVE columns, `path_*`) | Fingerprint confirmed. Dropping them removes 42% of rework PRs, so the class mix must be corrected (see FEATURES_LOG.md pass 1) |
| `head_from_fork` blank | Fork deleted later: 16% of nowhere vs 7% of clean | Fill blank as fork (at webhook time the fork exists) |
| `author_prs_merged_repo` blank | Blank only on merged PRs | Drop; our own PR history replaces it |
| `AGE` blank | Leans to goes-nowhere | Tested: legitimate, kept (below) |
| `observed_through` null on every goes-nowhere PR | Not a feature, but broke the maturity filter | Fill with the repo's cutoff |

`AGE` blank: **not** a leak. 99% of the blanks are PRs that only add new files (no file history); kept.

Checked and **not** a leak: `EXP`/`REXP`/`SEXP` are counted from commits before the at-open fork point. Across an
author's PRs they rise 55% of the time, stay 41%, fall 4% (an older fork point); only 9% of authors with 5+ PRs keep one
value. Their 0.99 correlation is by construction (REXP is EXP weighted by recency). `title_len`, `has_milestone`,
`declared_hotfix` are rebuilt as at open; `author_account_age_days` is at open; repo-wide rates count only PRs closed
before open.

## Earlier results that shaped the direction (before 2026-11-10)

- By PR, LightGBM + point-in-time author/repo history: skill 0.320 on outside PRs. By repo: about 0.19. Generalising to
  new repos is the limit.
- History computed over all PRs (not only outside ones) hurt outside PRs by about 0.02; extra miner columns did not
  transfer to new repos.
- Conditional label conventions (as `sample_output`): `p_label_rework = p_rework / p_merged`. The joint convention gave
  rework calibration error 0.13.
- Those results used Nishit's rows and some leaky inputs (recovered rows, `head_from_fork` blanks), so they are
  optimistic; superseded by the redo.

## 2026-11-10: after the EDA (`eda.ipynb`)

| Decision | Why |
|---|---|
| Both splits, final model for each | Confirmed by the user |
| Test training on all PRs vs outside only, with our own features | Earlier "no gain" was with Nishit's features |
| Test the recovered-row fingerprint (in the EDA notebook or a new one) | The user: whichever is neater |
| `label_bad`: predict both the miner's definition and Nishit's (revert OR hotfix); if both worsens the score, only Nishit's | The user |
| Feature engineering experiments before the pipeline notebook, starting with ideation | The user |

## Feature experiments (FEATURES_LOG.md has the detail)

1. Pass 1 set-up found two bugs in our own history code before any result was used:
   - Unknown authors were pooled under one key, and counts without a window did not subtract earlier keys. Caught by
     the new unit and cut tests (`test_fe_features.py`).
   - Timestamps from parquet are microseconds, not nanoseconds, so every time window (90, 180, 365 days) was really
     all-time, and the open hour was wrong. **The same bug is in the earlier `history.py`**, so its `hist_repo90_*`
     were all-time counts: the earlier results stand as measured, but the feature was not what its name said.
     Fixed in `fe_features.secs`, with a test for every unit.
2. Pass 1 (FEATURES_LOG.md) decided, in this order:
   - Recovered rows carry a fingerprint, so every config is also scored on exact diffs reweighted to each repo's
     class mix; that view is the one we compare on.
   - Keep the consolidated block (no loss, 14 fewer columns); add author history (+0.03 both splits) and repo
     history (+0.027 for new repos).
   - Drop context and cross-repo author history (within noise; the latter also needs an org-wide view).
   - Train on outside PRs only (+0.005 to +0.007).
   - Specialisation vs generalisation on 23 repos: a shared model beats one model per repo (0.283 vs 0.272); adding
     the repo as a feature helps on seen repos (0.290), so only the by-PR model may use it.
   - Dropping recovered rows outright broke calibration (0.034 / 0.040): pass 2 drops them with reweighting.
3. Pass 2 (FEATURES_LOG.md) decided:
   - Drop recovered rows and reweight exact rows to each repo's class mix (+0.008 / +0.007 on the exact view). This
     is now the default.
   - `author_association` alone was worth +0.11 to +0.12: nearly all of the leak inflation.
   - Re-checked on the new default, no group can be pruned (scanners, file history, repo-local and LLM all pay).
   - The extra history features (last outcomes, recent windows, relative size) add nothing; dropped.
   - The feature set is frozen at 161 columns for the architecture comparison; `repo` as a feature stays an option
     for the seen-repos model (+0.003).
4. Pass 3 compares architectures on the frozen set, because the early LightGBM-vs-network comparison was made on
   leaky features and Nishit's rows, and history (now the strongest signal) might be read better as a sequence.
   Learners: LightGBM (4 settings at learning rate 0.05), XGBoost, CatBoost, logistic regression, an MLP, and a GRU
   over the author's and the repo's previous PRs (`fe_seq.py`, with its own cut test). Blends follow.
5. Pass 3 found:
   - Boosting is still the best single model on clean features: LightGBM 0.294 / 0.240, XGBoost level, CatBoost
     behind, networks 0.02 to 0.03 behind. The sequence model beats the plain MLP but not the trees.
   - A geometric blend of LightGBM, XGBoost, the sequence model and the MLP adds about 0.005 (0.298 / 0.242,
     cross-fitted). Most of the new-repo gain comes from any second model smoothing the trees.
6. Pass 4 measured quality (3 seeds) and cost (training, serving, libraries) for nine setups:
   - The best on both splits is H, LightGBM + XGBoost + sequence model (0.297 / 0.242). Its costs are torch and a
     serving path that fetches each author's and repo's recent PRs; its explanations cover the trees only.
   - The best without torch: G, LightGBM + XGBoost + logistic regression (0.294 / 0.242); on new repos, D (LightGBM +
     logistic regression) gets the same 0.242 more cheaply.
   - F (all four) and E add nothing over H and C; dropped.
   - XGBoost on the GPU is not reproducible with a fixed seed (probabilities differ by up to 0.17); on the CPU it is,
     so it trains on the CPU.
7. The user chose **G (LightGBM + XGBoost + logistic regression) for both final models**: best new-repo score,
   0.003 behind H on seen repos, no torch, deterministic, every part explainable.
   - Plain-language numbers for G, for the writeup (skill is not accuracy), on the exact view with 3 classes:
     - Accuracy: 0.640 on seen repos and 0.605 on new repos, against 0.33 for a random guess and 0.36 for always
       guessing the most common outcome.
     - AUROC per class: 0.84 / 0.79 / 0.85 on seen repos, 0.79 / 0.74 / 0.82 on new repos.
     - When the model is at least 70% sure (34% / 24% of PRs), it is right 85% / 82% of the time.
     - The hardest case is telling rework from clean: 27-29% of rework PRs are predicted clean.
8. Pass 5, building the notebook, found a small leak in the experiment code by checking that the notebook's features
   matched `fe_features.py`:
   - 1,339 goes-nowhere PRs (0.7%) have no `closed_at`. The experiment code clamped their close time to their open
     time, so their outcome counted in every later PR's history (the author's next PR included) from the moment
     they opened.
   - Rule now in both: a PR with an unknown close time adds no outcome and no open/closed count to anyone's history;
     it still counts as having opened.
   - Passes 1 to 4 compared configs that all shared this leak, so their comparisons stand; their absolute numbers
     may be slightly optimistic. The notebook's results are the ones to report.
   - Also: the team's `label_bad` (revert OR hotfix, ADR 0021, Nishit's files) is what `p_label_bad` predicts, and
     `label_bad` / `label_bad_observed` in the output use that definition. The miner's broader `label_bad` is
     predicted as `p_label_bad_miner`, next to `label_bad_miner` / `label_bad_miner_observed`. ADR 0021 is not on
     main; its wording should be checked when it lands.
   - A label model with too few observed positives falls back to its training base rate, so no output column is
     ever blank.
9. The first full notebook run showed the reason model did not transfer: on unseen repos, `label_abandoned` skill was
   -0.085 (calibration error 0.085). A by-repo test (5 folds, seed 0) of the reason model:
   - current settings: -0.020, 11 of 23 repos below the base rate
   - heavy regularisation: -0.007
   - few features: -0.028
   - **half model, half training base rate: +0.014, 6 of 23 below**
   A share fitted on validation repos came out near 1 (the calibration is fitted on the same repos), so it is fixed:
   0.5 for unseen repos (the by-repo evaluation and `new_repos`), 1.0 for `seen_repos`. It was chosen on these same
   repos, so the by-repo reason scores are slightly optimistic. The label models are not shrunk: on unseen repos they
   already sit at the base rate (revert, bad) or keep real skill (SZZ bug 0.14).
10. Following item 8, passes 1 to 4 were rerun on leak-free features (FEATURES_LOG.md, "Rerun of passes 1 to 4"):
    - A new test, scrambling the outcomes of the rows with no `closed_at`, found that those rows are the miner's
      stale-open rows (still open when mined, labelled abandoned at mining; ARCHITECTURE.md). The test found the same
      leak in the sequence model and in the unused `s_rel_*` features. Both are fixed: a stale-open row counts as
      open at every earlier moment and closes after mining.
    - Every comparison came out the same. By repo, skill fell about 0.003 everywhere, the size of the leak. G and H
      still tie on new repos (0.239) and H leads by 0.002 on seen repos, so G stays the choice.
    - The notebook's leakage cell now runs the stale-open check too. The old results are kept in a
      local backup, outside the repo.
