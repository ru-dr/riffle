<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset=".github/assets/logo-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset=".github/assets/logo-light.svg">
  <img src=".github/assets/logo-light.svg" alt="Riffle" width="280">
</picture>

**Review what matters first.**

</div>

---

Riffle is an open-source GitHub App that ranks your pull request queue by
risk — trained on your repository's own revert history, not one vendor's
rules. Self-hosted, and nothing skips review.

> **Status: in development.** Not yet ready for production use.

## Why

Every repository breaks differently. A change to a config file might be
routine in one codebase and the most common cause of incidents in another.
Generic heuristics can't know the difference; your revert history can.

Riffle learns from what has actually broken in *your* repository and orders
the review queue accordingly — so the riskiest change gets the most careful
pair of eyes, and nothing gets waved through.

## Principles

- **Your history, not our rules.** Risk scores are trained per-repository.
- **Self-hosted.** Your code and history stay on infrastructure you control.
- **Nothing skips review.** Riffle reorders the queue; it never approves.

## License

[Apache-2.0](LICENSE)
