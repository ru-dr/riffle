Riffle is a GitHub App that orders a team's pull request queue by risk. When a
pull request opens, Riffle scores the change and places it in one of three
[rank bands](/docs/concepts/rank-bands), with a short explanation of what to
check first.

> **Riffle is in development.** The architecture, contracts and invariants on
> these pages are settled; the implementation is in progress.

## The problem

AI coding agents made writing code cheap. Reviewing it did not get cheaper. The
bottleneck moved from authoring to verification, and reviewer capacity did not
scale to meet it.

| Source | Finding |
| --- | --- |
| [Faros AI, *The Acceleration Whiplash*](https://www.faros.ai/research/ai-acceleration-whiplash) | Median review time up **441.5%**; bugs per developer up 54%; 31% more PRs merging with no human review |
| [LinearB, *2026 Benchmarks*](https://linearb.io/resources/software-engineering-benchmarks-report) | Across 8.1M PRs, AI-generated PRs wait **4.6×** longer for first review, and are accepted 32.7% of the time against 84.4% |
| [GitHub](https://github.blog/ai-and-ml/generative-ai/agent-pull-requests-are-everywhere-heres-how-to-review-them/) | More than **1 in 5** code reviews now involve an agent |

## What Riffle does

Riffle reorders the queue. It never merges a pull request, and it never removes
one from review. Nothing is auto-approved and nothing is hidden.

The difference is where the signal comes from. A change to a config file might
be routine in one codebase and the most common cause of incidents in another.
Generic heuristics cannot tell the difference, so Riffle learns from each
repository's own merge, revert and follow-up-fix history, on top of
[a global model](/docs/concepts/model) that works from the first pull request.

## What it guarantees

| Guarantee | In practice |
| --- | --- |
| Nothing skips review | Riffle ranks; humans still review every pull request |
| Fail open | If scoring fails, the PR still appears in the queue, unranked and flagged |
| The LLM is never on the correctness path | A missing explanation lowers quality, never the rank |
| Self-hosted | Code, diffs and author identities stay inside your installation |

## Where to go next

- [How it works](/docs/getting-started/how-it-works): the path of one pull request.
- [Rank bands](/docs/concepts/rank-bands): what each band means and how it is cut.
- [Configuration](/docs/configuration/overview): tune bands, floors and routing per repository.
