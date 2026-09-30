Every scored pull request lands in exactly one band. Bands are cut from the
repository's own score distribution by default, so "the riskiest 5%" means the
same thing in a quiet repository as in a busy one.

| Band | Default share | Meaning for the reviewer |
| --- | --- | --- |
| `senior_recommended` | Top 5% | The highest-risk changes. Route to someone who knows the paths involved |
| `review_first` | Top 20% | Read before the rest of the queue |
| `standard` | Everything else | Normal review, in the usual order |

A further 5% of pull requests are left **unranked**, in plain FIFO order, as a
holdout. It is how Riffle measures its own lift against the queue you would
have had without it, and it limits feedback-loop bias.

## Percentile or absolute

```yaml
# .github/riffle.yml
rank_bands:
  mode: percentile                     # default
  percentile:
    senior_recommended_top_fraction: 0.05
    review_first_top_fraction: 0.20    # matches the top-20% effort-aware recall metric
    window: repo                       # or component: rank each monorepo package against itself
```

`absolute` mode cuts on fixed scores instead: `senior_recommended_min_score`
defaults to `0.8`, `review_first_min_score` to `0.5`.

| Key | Allowed | Default |
| --- | --- | --- |
| `rank_bands.mode` | `percentile`, `absolute` | `percentile` |
| `rank_bands.holdout_fraction` | `0.02`–`0.1` | `0.05` |
| `rank_bands.tie_break` | ordered list of tie-break signals | `similar_pr_bad_rate`, `path_detection_lag_days` |

Tie-breaks apply only to identical rounded scores; pull request age is always
the final key.

## Floors

A floor raises the minimum band for a path, whatever the score says:

```yaml
floors:
  paths:
    - { pattern: "src/auth/**", min_band: senior_recommended }
```

> **Proposed.** Band floors are specified but still awaiting a decision
> record before their defaults are final.

Every key is listed in the [configuration reference](/docs/configuration/reference).
