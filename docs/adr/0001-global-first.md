# 0001: Global model first, per-repo layer on top

- **Date:** 2026-09-29
- **Status:** Accepted
- **Owners:** Modelling workstreams #2 and #3

## Context

v0.1 trained one model per repository. A small repository sees a handful of
reverts a year, so a per-repo model has nothing to learn from until long after
install. That is a cold-start problem, and it leaves most repositories with no
useful ranking on day one.

## Decision

- One global gradient-boosted model serves every repository from the first PR.
  It trains on published and self-mined data, plus opt-in shared outcomes.
- Each repository gets a lightweight per-repo layer on top, fitted on its own
  mature outcomes. It fits on CPU in seconds and works with dozens of labels.
- `layer_weight` starts at 0.0 and grows with the number of mature labels.
  There is no cutover and no threshold to argue about.
- Each layer is checked against its own holdout. If it ranks worse than the
  global model alone, its weight drops to 0.0 automatically.
- Path history is computed from git on install, so repository-specific signal
  exists before any layer is trained.
- On install, history is backfilled recent-first, so a large repository's layer
  can train within hours rather than waiting months for new outcomes.

## Options not taken

| Option | Why not |
| --- | --- |
| Per-repo model only (v0.1) | Cold start: too few labels in most repositories |
| Per-repo model with a hard cutover from the global model | A threshold nobody can justify, and a visible jump in behaviour at cutover |
| Global model only | Within-project prediction beats cross-project in the literature; this is also the product's differentiator |

## Consequences

- The per-repo layer is the headline claim. Kill criterion: if global plus
  layer does not beat both the global model and FIFO in the backtest, Riffle
  repositions as an explanation layer.
- `model_version` pins the global model, the layer, the extractor, and the
  compiled rules hash together.

## Open

How `layer_weight` grows: fixed ramp by label count, learned from holdout, or
a hybrid (ramp sets the ceiling, holdout can only lower it). The hybrid is
recommended, with the ramp constant tuned in the backtest. Record the choice
as a follow-up ADR.
