### Newcomer threshold mitigation (by repo; 67,550 PRs, 31,765 by newcomers)

| policy | p_nowhere multiplier | false flags, newcomers | false flags, returning | nowhere caught, newcomers | accuracy, newcomers | accuracy, all | macro F1, all | calibration error, newcomers |
|---|---|---|---|---|---|---|---|---|
| none | 1.000 | 34.5% | 10.1% | 72.2% | 58.8% | 60.8% | 0.604 | 0.022 |
| unflag | 0.774 | 24.0% | 10.1% | 62.8% | 58.8% | 60.8% | 0.606 | 0.022 |
| unflag (shifted probabilities) | 0.774 | 24.0% | 10.1% | 62.8% | 58.8% | 60.8% | 0.606 | 0.046 |
| parity | 0.487 | 10.1% | 10.1% | 45.6% | 56.8% | 59.8% | 0.595 | 0.022 |
| parity (shifted probabilities) | 0.487 | 10.1% | 10.1% | 45.6% | 56.8% | 59.8% | 0.595 | 0.128 |
