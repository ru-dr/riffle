Riffle does not wait for a repository to accumulate history before it can rank.
One global model serves every repository from the first pull request, and each
repository earns its own adjustment as outcomes arrive.

> **Accepted.** This design is recorded and accepted.

## Two layers

| Layer | Trained on | Serves | Fits in |
| --- | --- | --- | --- |
| Global model | Published datasets, self-mined history, opt-in shared outcomes | Every repository, from day one | A scheduled training run |
| Repository layer | That repository's own mature outcomes | That repository only | Seconds, on CPU, with dozens of labels |

The global model is gradient-boosted. The repository layer is deliberately
light, so it can be refitted often and cheaply.

## How the repository layer earns its weight

```text
layer_weight
   1.0 ┤                          ╭──────────
       │                     ╭────╯
       │               ╭─────╯
       │         ╭─────╯
   0.0 ┼─────────╯
       └────────────────────────────────────▶ mature labels
```

- `layer_weight` starts at `0.0` and grows with the number of mature labels.
  There is no cutover and no threshold to argue about.
- Each layer is checked against its own holdout. If it ranks worse than the
  global model alone, its weight drops to `0.0` automatically.
- Path history is computed from git on install, so repository-specific signal
  exists before the first label matures.

## Version pinning

A score records exactly what produced it. `model_version` covers the global
model, the repository layer, the feature extractor and the compiled rules
hash. A request never mixes a new model with an old feature extractor.

## Training corpus

The global model's planned corpus is 50 public repositories across five
domains, about 1.9 million pull requests measured.

| Domain | Pull requests | Share |
| --- | --- | --- |
| ML and end-user software | 505,272 | 27% |
| Systems and developer tools | 437,114 | 23% |
| Data systems | 425,940 | 22% |
| Cloud and infrastructure | 272,176 | 14% |
| Web frameworks | 259,494 | 14% |

Forty repositories train the model; ten are held out entirely, to measure how
it ranks repositories it has never seen. The target there is a ROC-AUC of
0.65 or better, following
[Kamei et al., 2016](https://link.springer.com/article/10.1007/s10664-015-9400-x).
