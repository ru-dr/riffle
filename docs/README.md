# docs

Engineering documentation. Written as decisions get made, not up front.

Suggested shape:

| File | Holds |
| --- | --- |
| `adr/` | One file per architectural decision, dated, with the option not taken |
| `contracts.md` | Schema version history and migration notes |
| `runbooks/` | On-call: queue backing up, explainer down, training stuck |
| `observability.md` | What each `riffle_` metric means and when to page |
| `onboarding.md` | Getting a local stack scoring a fixture PR |

Invariants and architecture live in the root `README.md` — the settled parts
stay there so they are the first thing a reader meets.
