# ADR prompt

Paste everything below the line into Claude (or any assistant), fill in the
three fields under **Input**, and send. In a coding agent working inside the
repo, you can instead say: "Write an ADR for <decision>, following
docs/adr/PROMPT.md."

---

You are writing an Architecture Decision Record (ADR) for Riffle, a self-hosted
GitHub App that ranks pull requests by learned risk. ADRs live in `docs/adr/`,
one file per decision.

## File
- Name: `docs/adr/NNNN-short-kebab-title.md`, where NNNN is the next free
  4-digit number. If you can see `docs/adr/`, check it; otherwise use the
  number given in the input. Never reuse or renumber.
- One decision per file. If the input covers two decisions, write two ADRs.

## Format (follow exactly)

# NNNN: Title in plain words

- **Date:** YYYY-MM-DD
- **Status:** Proposed | Accepted | Superseded by NNNN | Rejected
- **Owners:** workstream number and name, e.g. #2 Training

## Context
What problem forced the decision. Facts and constraints only, 3 to 6 sentences.

## Decision
What we will do, as short bullets. Concrete values, names, and defaults.
Link any schema, contract, or file it changes by repo path.

## Options not taken
| Option | Why not |
| --- | --- |
Every option the team seriously considered, one line each.

## Consequences
What changes because of this: code, contracts, metrics, workstreams affected.

## Revisit if
Specific, checkable conditions that would reopen the decision.

## Open
Anything still unknown. Omit the section if nothing is.

## Rules
- Status is **Proposed** while it is still under discussion, and **Accepted**
  only if the input says the owners agreed. Never mark it Accepted on your own.
- Do not edit an Accepted ADR's decision. Write a new ADR that supersedes it,
  and change only the old one's Status line to "Superseded by NNNN".
- Plain, short language. British spelling (organisation, behaviour). No emoji,
  no marketing tone, no academic framing.
- Do not invent facts, numbers, or agreements. Put unknowns under Open.
- Respect Riffle's invariants: the LLM is never on the scoring path, nothing
  skips review, Riffle never blocks merges, nothing leaves the installation by
  default, and every score is point-in-time with no leakage. If the decision
  conflicts with one, say so in Consequences.
- Output only the file path on the first line, then the ADR markdown.

## Input
ADR number: <next free number, e.g. 0003>
Decision or discussion: <what was decided, or what is being debated, and whether owners agreed>
Options considered: <list, with any reasons given>
Owners and date: <workstream, YYYY-MM-DD>
