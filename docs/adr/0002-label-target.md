# 0002: Training label target

- **Date:** 2026-09-29
- **Status:** Proposed
- **Owners:** Training workstream #2

## Context

The per-repo layer and the global model need one agreed definition of a bad
PR. Candidate sources differ a lot in precision: explicit fixes (reverts,
hotfixes, broken builds) are reliable, SZZ tops out near 0.6 precision, and
"any PR on the same lines within 14 days" also catches normal iteration. Old
CI results may have expired, so some sources are unobserved for older PRs.

## Decision

- Target: `label_bad = revert OR ci_fail OR hotfix`, binary.
  - `revert`: a later commit reverts the PR within 30 days.
  - `ci_fail`: required checks on the merge commit fail. Auxiliary and flaky
    checks are excluded.
  - `hotfix`: a hotfix-labelled PR touches the same lines within 7 days.
- SZZ and `follow_up_fix` are mined but not in the target. Both are reported
  as ablations.
- Maturity: 30 days, or 90 for SZZ. Younger PRs are dropped.
- Unobserved sources: `downweight`. An observed source that fired still makes
  the row positive. Otherwise the row counts as clean at weight 0.5 per missing
  source.
- The global model uses this definition only. Org label weights apply as
  sample weights in that org's per-repo layer, never in the global model.

Schema: `labels` block and `label_source` rules in
`contracts/org_rules.schema.json`. Defaults match this record.

## Options not taken

| Option | Why not |
| --- | --- |
| Include SZZ in the target | About 0.6 precision; adds noisy positives |
| Include `follow_up_fix` in the target | Catches review tweaks, not only bugs |
| Weighted target in the global model | Org weights differ, so shared outcomes would mix targets |
| Drop PRs with unobserved sources (`mask`) | Loses most older history |
| Treat unobserved as clean (`negative`) | Teaches broken PRs as safe |

## Ablations reported in the backtest

- `label_bad` alone (the headline number)
- `label_bad OR szz`
- `label_bad OR follow_up_fix`
- `unobserved`: mask vs negative vs downweight 0.5

## Evaluation note

ApacheJIT's bug-inducing labels are SZZ labels. The headline backtest mines
`label_bad` on the same Apache repositories and evaluates on that, so the model
is graded against the target it learned. Results against ApacheJIT's labels
are reported alongside.

## Revisit if

- Positives are too rare to train on in most repositories (under about 2%).
- An ablation beats the headline on effort-aware recall by a clear margin.
