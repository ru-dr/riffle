# 0021: Breakage target without CI: revert or hotfix

- **Date:** 2026-11-09
- **Status:** Proposed
- **Owners:** #2 Model training and tracking (the owner of ADR 0002), with #1 Labels and features

## Context

`pipelines/training/train_breakage.py --check-labels` on `data/training/train_at_open.csv` (23 repos, 37,517 usable merged PRs) found 768 bad PRs under ADR 0002: 682 of 30,784 in the training repos (2.2%) and 86 of 6,733 in the test repos (1.3%). 14 of the 23 repos are under 2%, which is ADR 0002's own condition for revisiting the target. CI results on the merge commit were observed for only 6% of merged PRs, mostly recent ones, because older CI history was not kept, yet CI failures account for 501 of the 768 bad PRs. So whether a PR has CI history alone predicts `label_bad` well (about 0.8 AUROC, estimated from these counts), and a model trained on it learns how recent a PR is: the same date leak `prepare_training_data.py` already removed from `max_file_post_merge_ci_fail_rate`. Reverts and hotfixes are observed for every mature merged PR, and the miner's `label_bad` matches ADR 0002's definition exactly (0 disagreements).

## Decision

- **The breakage target is `label_bad = revert OR hotfix`**, binary, on merged PRs only.
  - `revert`: a later commit reverts the PR within 30 days.
  - `hotfix`: a hotfix-labelled PR touches the same lines within 7 days.
- **CI on the merge commit (`label_ci_fail`) is not in the target.** It is still mined. It is not trained as a breakage model, and it no longer downweights clean rows.
- **Carried over from ADR 0002 unchanged:** maturity of 30 days (90 for SZZ); SZZ and `follow_up_fix` mined and reported only as ablations; `downweight` for unobserved sources (it changes no row today, since both sources are observed for every mature PR); org label weights only in an org's per-repo layer, never in the global model.
- **Until the miner writes the new definition, training rebuilds the label from its sources** with `pipelines/training/train_breakage.py --sources revert hotfix`. The script also rebuilds `label_bad` and `label_bad_observed` in the file the team evaluation scores, so a model is judged against the label it learned. On acceptance, the default of `--sources` becomes `revert hotfix`.
- **Every breakage run reports a "has CI history" baseline**, and warns when it predicts the label with an AUROC above 0.65, because the label then follows the date.
- **The PR's own CI result is a post-open signal.** It belongs in re-scoring once the PR's CI has finished, not in the at-open models and not in the target.

## Options not taken

| Option | Why not |
| --- | --- |
| Keep ADR 0002's target, unobserved CI downweighted | 501 of the 768 bad PRs come from the 6% with CI history, so the model learns recency |
| `--unobserved mask`: only rows with every source observed | 2,110 training PRs, all recent, mostly CI failures |
| `--unobserved negative`: unobserved CI counted as clean | The same date leak, and it teaches broken PRs as safe |
| SZZ as the target | About 0.6 precision (ADR 0002); 4,875 training positives, but noisy ones |
| Revert only | Hotfixes are rare (28 in the training repos) but the same kind of escaped breakage; keeping them costs nothing |
| No breakage model | The `risk_first` ranking (ADR 0019) and a risk-based `senior_recommended` band need P(bad given merged) |

## Consequences

- **Positives:** at most 208 bad PRs in the training repos and 67 in the test repos (revert 180 and 60, hotfix 28 and 7, before overlap), about 0.7% of merged PRs. Expect modest discrimination.
- **The test result rests mostly on one repo:** 49 of the 60 test reverts are in facebook/react. Report the cross-validation by repo (`--cv`) next to the test split.
- **Contract and miner:** `label_bad` and `label_bad_observed` in `contracts/mined_dataset.schema.json` and `pipelines/ingestion/repo_miner/labels.py` should follow the new definition in a later miner release. Until then they keep ADR 0002's meaning, and training rebuilds them.
- **Evaluation:** `pipelines/evaluation/eval_script.py` scores `label_bad` on rows where `label_bad_observed` is true. With the rebuilt columns, that is every mature merged PR rather than only the 6% with CI history.
- **ADR 0002:** on acceptance, only its Status line changes, to "Superseded by 0021". This record carries over the rules of ADR 0002 that still hold, so nothing is lost.
- **Invariants:** no conflict. Every score stays point-in-time, and removing CI from the target removes a date leak.

## Revisit if

- CI results on merge commits are mined for most merged PRs, and a hand-checked sample of failures shows they come from required checks.
- The revert OR hotfix model does not beat the repo's own earlier rate, on the test repos or in cross-validation by repo.

## Open

- **The CI failure rate:** where CI was observed, 481 of 2,110 merge commits in the training repos failed required checks (23%). That is high enough that non-required checks or post-merge workflows may be counted; it needs a hand-checked sample before CI is used again.
- **Older CI results:** whether the miner can recover them for older merge commits at all, and from what date.
- **Hotfix detection:** whether it should look beyond hotfix labels, which these repos rarely use (35 hotfixes in 37,517 merged PRs).
