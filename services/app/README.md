# app (TypeScript)

The GitHub App and the Next.js dashboard. The only service that talks to
GitHub or to humans: installation callbacks, check runs, PR comments, and the
queue UI.

Reads `ScoreResult` (`contracts/score_result.schema.json`). A result with a
null `explanation` renders without one — it is still a valid rank.

Riffle reorders the queue. This service never merges a PR and never removes
one from review.

> Not scaffolded yet. `next` conventions in this repo differ from upstream
> defaults — read `node_modules/next/dist/docs/` before adding code here.
