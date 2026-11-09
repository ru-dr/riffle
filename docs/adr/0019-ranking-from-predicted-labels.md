# 0019: Queue ranking from the predicted labels: two presets and a custom formula

- **Date:** 2026-11-07
- **Status:** Proposed
- **Owners:** #2 Training (the owner of the scorer and queue to confirm, see Open)

## Context

The outcome model (`lgbm_history`) returns P(lands_clean), P(lands_after_rework) and P(goes_nowhere) for every PR at open. The why model returns P(reason | goes nowhere) over abandoned, duplicate, unwanted, ci_technical and unknown. ADR 0002 defines `label_bad` (breakage after merge) and ADR 0009 says a combined risk, if wanted, is P(merge) x P(bad | merged). No contract says how these probabilities become the queue order: `rank_bands`, `floors` and `queue` in `contracts/org_config.schema.json` act on a `risk_score` that is never defined from them, and `score_result.schema.json` is not written yet (ADR 0009). The narrator (`pipelines/narrator/reasons.py`) assumes review score = P(lands_clean) + 0.6 x P(lands_after_rework), taken from the evaluation script's queue gains, which is a team choice recorded nowhere. Maintainers want different orders: some want likely-good PRs first to clear them quickly, others want the riskiest PRs first.

## Decision

- **Every scored PR reports every predicted label**, in a new `contracts/score_result.schema.json`:
  - Outcome: `p_lands_clean`, `p_lands_after_rework`, `p_goes_nowhere`, and `p_merge` = `p_lands_clean` + `p_lands_after_rework`.
  - Why: `p_reason_abandoned`, `p_reason_duplicate`, `p_reason_unwanted`, `p_reason_ci_technical`, `p_reason_unknown` (each given that the PR goes nowhere, summing to 1), plus `p_rejected` = `p_goes_nowhere` x (duplicate + unwanted + ci_technical) and `p_abandoned` = `p_goes_nowhere` x abandoned.
  - Risk: `p_bad_given_merged` (null until a `label_bad` model is served) and `risk` = `p_merge` x `p_bad_given_merged` (ADR 0009).
  - Ranking: `rank_score`, `rank_mode`, and the model versions used.
- **The queue order is a setting**, a new `ranking` block in `.github/riffle.yml` (`contracts/org_config.schema.json`), with two presets and a custom formula:

  ```yaml
  ranking:
    mode: merge_first          # merge_first | risk_first | custom
    custom:
      score: "p_lands_clean + 0.6 * p_lands_after_rework - 0.2 * p_rejected"
  ```

  - `merge_first` (default): `rank_score` = 1.0 x `p_lands_clean` + 0.6 x `p_lands_after_rework`. PRs most likely to land, and land cleanly, come first. The weights match the evaluation script's queue gains.
  - `risk_first`: `rank_score` = `p_merge` x `p_bad_given_merged`. PRs likely to be merged and likely to break something come first. Only available while a `label_bad` model is served; otherwise the setting falls back to `merge_first`, and the PR comment and audit log say so.
  - `custom`: a formula over the variables listed above (only those names), numbers, `+ - * /`, brackets, `min`, `max` and `clip`. It is parsed by a small expression grammar, never run as code. It is checked when the config loads: an unknown name or a syntax error rejects the new config and keeps the previous one, with the error in the audit log. The result is clipped to 0 to 1, so `rank_bands.absolute` keeps working.
- **Ranking never changes what the model sees or learns.** It lives in `org_config`, not `org_rules`, so it is not hashed into `model_version`, and changing it needs no retrain. Every change of `ranking` is written to the audit log.
- `rank_bands`, `floors` and `queue` (epic priority, manual boosts) apply to `rank_score` after the formula, as they do to `risk_score` today. The "likely to go nowhere" list stays separate, ordered by `p_goes_nowhere`, and shows rejected vs abandoned and the most likely reason.
- The narrator explains the active formula. `reasons.py` takes the formula instead of the fixed 0.6, so its contributions add up to the `rank_score` the maintainer sees. This is exact for weighted sums; for products and custom formulas the same integrated-gradients method is used.
- **Model selection does not depend on any repo's setting.** Models are always compared on the evaluation script's fixed queue gains (the `merge_first` weights) and its other metrics.

## Options not taken

| Option | Why not |
| --- | --- |
| One fixed formula for everyone | Maintainers want different orders; the probabilities already support both |
| A custom Python function in `riffle.yml` | Running code from repo files inside the app is a security risk |
| Put ranking in `org_rules` | Ranking does not change what the model learns; hashing it into `model_version` would force retrains for an ordering choice |
| A separate model per ranking mode | The same probabilities serve every mode; extra models add cost and drift for no gain |
| Learn the weights per repo from maintainer behaviour | There is no review-order feedback data yet |

## Consequences

- New contract `contracts/score_result.schema.json`. `contracts/org_config.schema.json` gains the `ranking` block, which is a config schema version bump (ADR 0006). `docs/contracts.md` and `contracts/REFERENCE.md` need regenerating.
- The scorer computes `rank_score` from `score_result`. `rank_bands` and `pr_output.show_score` refer to `rank_score`; `risk_score` keeps its ADR 0009 meaning (`risk`).
- `pipelines/narrator/reasons.py` reads the active formula. `pipelines/training/train_why.py` already produces the why probabilities.
- `risk_first` depends on a `label_bad` model (ADR 0002), which is not trained yet.
- Invariants: the formula is deterministic, so no LLM is on the scoring path. Floors still apply, so nothing skips review. Riffle still never blocks merges, nothing leaves the installation, and every input is a point-in-time prediction. No conflict.

## Revisit if

- Most installations use `custom` with the same formula, which then becomes a preset.
- A `label_bad` model ships and its backtest does not rank breaking PRs above others, so `risk_first` adds nothing.
- Review-order data shows a different rework weight than 0.6 serves maintainers better.

## Open

- Which workstream owns the scorer, the queue and this setting.
- Whether `risk_first` should offer an interim score before a `label_bad` model exists (for example `p_merge` x the LLM risk flag). That would be a rule, not a prediction, and would need labelling as such.
- The exact formula grammar and its list of functions.
- What the band names mean under each mode: `senior_recommended` reads as "riskiest" under `risk_first` but as "best" under `merge_first`.
- Whether the age adjustment of the live queue (how long a PR has been open) applies before or after the formula.
