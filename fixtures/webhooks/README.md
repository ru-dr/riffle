# webhooks

Sample GitHub webhook payloads, one file per `<event>.<action>.json`. They are GitHub's format,
not Riffle's: what intake makes from them is checked against `contracts/` by intake's tests.
They can also be sent, signed with the webhook secret, to a running intake to test it without
the GitHub App (`services/intake/README.md`).

Every payload is for the fixture installation `48213` (`acme-corp`). Only the fields Riffle
reads, plus a few for realism, are included; add a field here when code starts reading it.

| File | Exercises |
| --- | --- |
| `pull_request.opened.json`, `pull_request.synchronize.json` | A `PrEvent` |
| `pull_request.closed.json` | An ignored action |
| `check_suite.completed.json` | One delivery naming two PRs (fan-out) |
| `check_suite.completed.fork.json` | A check suite with no PRs (a fork) |
| `installation.created.json`, `installation.deleted.json`, `installation_repositories.removed.json` | `InstallationEvent` |
| `ping.json` | GitHub's test delivery |
