# 0023: The at-open outcome model: setup G, with one model for seen repos and one for new repos

- **Date:** 2026-11-14
- **Status:** Proposed
- **Owners:** #2 Training

## Context

The team needs one model that predicts `outcome_3class` (ADR 0009) when a PR opens, with the reason and post-merge
label probabilities in the team's `sample_output.csv` columns, judged by `pipelines/evaluation/eval_script.py`. The
features and leak rules are ADR 0022. On the v0.1 team data (23 repos, 192,998 PRs), passes 3 and 4 compared
LightGBM settings, XGBoost, CatBoost, logistic regression, an MLP, a GRU over each author's and repo's previous PRs,
and eight blends of them, over 3 seeds and two splits: by PR (a 20% hold-out) and by repo (5 folds of whole repos).
A scorer may meet repos it was trained on or repos it has never seen.

## Decision

- **Outcome model, setup G:** LightGBM, XGBoost and logistic regression, each calibrated by vector scaling on
  validation rows, combined by a geometric blend whose weights, temperature and biases are fitted on the same rows.
  Three seeds, averaged at scoring time. XGBoost trains on the CPU.
- **Two final models:** `seen_repos`, validated on 15% of PRs, and `new_repos`, validated on 15% of whole repos (at
  least 2), so its calibration reflects repos it has not seen.
- **Reasons:** a LightGBM over the five `nowhere_reason` values on goes-nowhere PRs, vector-scaled. For repos the
  model has not seen (`new_repos` and the by-repo evaluation), its probabilities are half model, half training base
  rate.
- **Post-merge labels:** one heavily regularised LightGBM per label, Platt-scaled, on merged PRs whose label was
  observed. A label with too few observed positives falls back to its training base rate. `p_label_bad` follows
  the team's definition (revert or hotfix, ADR 0021); the miner's broader `label_bad` is `p_label_bad_miner`.
- **Output:** `sample_output.csv`'s 56 columns in order, then `p_label_hotfix`, `p_label_bad_miner`,
  `label_bad_miner`, `label_bad_miner_observed`.
- **Code:** `pipelines/training/outcome/pipeline.ipynb`, standalone. Models are saved as JSON, LightGBM text and
  XGBoost UBJ (no pickles) and are not in git.

## Options not taken

| Option | Why not |
| --- | --- |
| H: LightGBM + XGBoost + the GRU sequence model | Ties G on new repos (0.239) and leads by 0.002 on seen repos (0.297 against 0.295); needs torch, a serving path that fetches recent PRs, and explains only its tree part |
| F: LightGBM + XGBoost + GRU + MLP | No better than H (0.297 / 0.239) |
| LightGBM alone | 0.293 / 0.235 |
| CatBoost, the MLP, logistic regression alone | Each behind LightGBM alone (one seed: 0.287 / 0.229, 0.264 / 0.219, 0.242 / 0.215) |
| One model per repo | 0.273 against 0.292 for one shared model that knows the repo (ADR 0022) |
| XGBoost on the GPU | Not reproducible: the same seed gave probabilities up to 0.17 apart |

Skill is against the base rate, averaged over repos, on exact at-open diffs (by PR / by repo, 3 seeds).

## Consequences

- **Results** (by PR / by repo): skill 0.296 / 0.237; accuracy 64.4% / 60.3% (36% / 35% for always guessing the
  most common outcome); 84.6% / 81.5% right when at least 70% sure; `eval_script.py` skill 0.269 / 0.215 with
  calibration error 0.015 / 0.014. In the team's paired comparison on 64,788 PRs it scored skill 0.205 against
  0.192 for the next best.
- **Cost:** 75 s to train one seed on the CPU, 271 MB of libraries (LightGBM, XGBoost, scikit-learn), about 30 ms
  for one PR at a time and about 1.2 ms per PR in batches of 256, features included; no GPU.
- **Invariants:** scores are point-in-time (ADR 0022's tests run in the notebook). The LLM is not on the scoring
  path: its features may be null. The model ranks and explains; it does not merge, approve or hide a PR.
- **Evaluation:** both splits run through `eval_script.py`; the by-repo split passes every check it can run.

## Revisit if

- The full corpus (43 repos) or a time-based split changes the ranking of setups.
- A sequence model gains more than seed noise on new repos, or torch is needed for another reason.
- The team's evaluation adds checks G fails.

## Open

- Reason quality: in the team comparison the reason F1 was 0.17 against 0.41 for the best, and the model rarely
  predicted `unknown` (0.9% against 24.4% of PRs). The cause is not confirmed; how `unknown` is named in the reason
  columns is the first thing to check.
- `p_label_ci_technical` is badly calibrated (error 0.14): the label is filled only where CI was observed.
- Revert, `label_bad` and hotfix have too few positives to rank on new repos.
- The heuristic baseline the evaluation compares against is not settled.
- Whether one model could serve both seen and new repos as well as two was not measured.
