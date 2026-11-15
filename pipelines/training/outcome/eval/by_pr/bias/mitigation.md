### Newcomer threshold mitigation (by pr; 13,569 PRs, 6,397 by newcomers)

| policy | p_nowhere multiplier | false flags, newcomers | false flags, returning | nowhere caught, newcomers | accuracy, newcomers | accuracy, all | macro F1, all | calibration error, newcomers |
|---|---|---|---|---|---|---|---|---|
| none | 1.000 | 26.2% | 7.6% | 69.2% | 61.3% | 63.9% | 0.635 | 0.017 |
| unflag | 0.777 | 18.2% | 7.6% | 61.3% | 61.0% | 63.7% | 0.634 | 0.017 |
| unflag (shifted probabilities) | 0.777 | 18.2% | 7.6% | 61.3% | 61.0% | 63.7% | 0.634 | 0.047 |
| parity | 0.449 | 7.6% | 7.6% | 45.8% | 58.5% | 62.5% | 0.622 | 0.017 |
| parity (shifted probabilities) | 0.449 | 7.6% | 7.6% | 45.8% | 58.5% | 62.5% | 0.622 | 0.130 |
