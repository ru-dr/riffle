# NNNN: Title in plain words

<!--
How to use
- Copy this file to docs/adr/NNNN-short-kebab-title.md.
  NNNN is the next free 4-digit number in docs/adr/. Never reuse or renumber.
- One decision per file.
- Status is Proposed while under discussion. Accepted only after the owners agree.
- Never edit an Accepted decision. Write a new ADR that supersedes it, and change
  only the old one's Status line to "Superseded by NNNN".
- Plain, short language. British spelling. No emoji.
- Do not invent facts or numbers. Put unknowns under "Open".
- Check the invariants: the LLM is never on the scoring path, nothing skips
  review, Riffle never blocks merges, nothing leaves the installation by
  default, every score is point-in-time. Note any conflict under Consequences.
- Delete this comment before committing.
-->

- **Date:** YYYY-MM-DD
- **Status:** Proposed
- **Owners:** #N Workstream name

## Context

What problem forced the decision. Facts and constraints only, 3 to 6 sentences.

## Decision

- What we will do, with concrete values, names, and defaults.
- Link any schema, contract, or file it changes by repo path, e.g.
  `contracts/org_rules.schema.json`.

## Options not taken

| Option | Why not |
| --- | --- |
| | |

## Consequences

- What changes because of this: code, contracts, metrics, workstreams affected.

## Revisit if

- A specific, checkable condition that would reopen this decision.

## Open

- Anything still unknown. Delete this section if nothing is.
