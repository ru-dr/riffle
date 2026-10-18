# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@AGENTS.md

The Next.js note above applies to `site/` only. That is where the `next` package and `node_modules/next/dist/docs/` live.

## What exists today vs. what is planned

Riffle is a self-hosted GitHub App that ranks pull requests by learned risk. The root `README.md` describes the target system: four services, pipelines, `libs/features/`, a `Makefile`, `.env.example` and `docs/handbook.md`. **Most of that does not exist yet.** At the moment the repo contains:

- `contracts/`: JSON Schemas (draft 2020-12), the real source of truth, plus `gen_reference.py`.
- `docs/adr/`: numbered decision records. `docs/contracts.md` holds the schema version history.
- `fixtures/config/`: example `riffle.yml` files that must validate against the schemas.
- `site/`: the marketing page and `/docs` (Next.js 16, React 19, Tailwind 4, Bun). This is the only part that builds, and the only part CI checks.
- `services/*`, `pipelines/*`, `infra/*`: these contain only a `README.md` each. Each one says what belongs in the directory and which invariants it owns, so read it before adding code there.

`make bootstrap/up/seed/test/lint` and `make replay`/`make train` are documented but not implemented. Don't assume they work.

## Commands

The site (run from `site/`; Bun 1.3.x, Node 22):

```bash
bun install --frozen-lockfile   # what CI runs; fails if bun.lock and package.json disagree
bun dev                         # runs scripts/build-docs.mjs, then next dev
bun run build                   # build-docs + next build; this is the whole CI check (typecheck + build + OG images)
bun run docs                    # regenerate components/docs/content.generated.ts only
bun run brand                   # regenerate brand icons from source SVGs
```

There are no tests or linter yet. `bun run build` is the verification step.

Contracts (run from the repo root):

```bash
python contracts/gen_reference.py           # regenerate contracts/REFERENCE.md after any schema change
python contracts/gen_reference.py --check   # fails if REFERENCE.md is stale
```

## Site architecture

- **Docs content pipeline:** `site/scripts/build-docs.mjs` compiles `site/content/docs/*.md` and `contracts/REFERENCE.md` into `site/components/docs/content.generated.ts`. That file is gitignored and generated, so never edit it. Pages import it statically on purpose: reading files from computed paths made Turbopack trace the whole repo into the server bundle.
- **`site/components/docs/registry.ts` is the docs information architecture.** The sidebar, landing cards, prev/next pager and static routes all derive from `SECTIONS`. A page is added there, with `file` (under `content/docs`), `source` (a repo file rendered as-is, e.g. `REFERENCE.md`) or `outline` (a planned page rendered as a draft). Optionally it gets a `status` (`accepted | proposed | planned | generated | draft`).
- Markdown renders through `components/docs/markdown.tsx` (react-markdown + remark-gfm + rehype-slug, Shiki highlighting). ` ```mermaid ` blocks render via `beautiful-mermaid`.
- `/docs/search.json` serves a MiniSearch index. `/docs/ask` is "Ask AI" on Cloudflare Workers AI: it reads env `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_AI_TOKEN` and optionally `DOCS_ASK_*`, and its rate limits are kept in memory.
- `site/content/docs/*.md` restates material from the root README and `contracts/README.md` for readers. When you change an invariant, contract or architecture fact, update both places.
- Deployed on Vercel with Root Directory = `site`.

## Contracts rules

`contracts/README.md` is authoritative. The rules that matter most when editing:

- Any schema change is breaking. Add new fields as optional, backfill, then make them required. Bump every consumer in the same PR.
- After changing a schema, regenerate `REFERENCE.md`, add a row to `docs/contracts.md`, and bump `x-riffle-version`. Minor versions add optional keys or change defaults; major versions rename or remove keys.
- `x-riffle-*` keywords (merge strategy, org-only, sink, model columns, phase, decision-pending, code-checks) carry semantics that loaders and the compiler rely on. Preserve them.
- `null` means "not declared / not enough history" and `0` means "measured, found none". Never coerce one into the other. `explanation` stays nullable.
- Settings (`org_config`) never change what the model sees. Anything that does belongs in `org_rules`, which is hashed into `model_version`.

## Invariants (from README; breaking one is a bug even if tests pass)

- Scoring is idempotent per `delivery_id`.
- One pinned `model_version` per request.
- Scoring is point-in-time, with no leakage.
- Tenants get fair shares.
- Training runs under backpressure.
- The system fails open: an unranked PR is still shown.
- The explainer is optional, and the LLM is never on the scoring/correctness path.
- Nothing leaves the installation by default.
- Riffle never merges, auto-approves or hides a PR.

## Workflow conventions

- **Branch names are enforced by CI (ADR 0003):** `<prefix>/<kebab-description>`, using one of these prefixes: `intake scorer explainer app miner compiler pipelines infra site docs ci chore fix deps`.
- Every change goes through a PR. Merges are squash only, and branches are rebased on `main`, not merged.
- Changes to `contracts/` need approval from every affected owner (see `.github/CODEOWNERS`).
- A PR that implements a decision needs its ADR. Write ADRs following `docs/adr/PROMPT.md` exactly:
  - Use the next free `NNNN` and never renumber.
  - Status stays `Proposed` unless the owners agreed.
  - Never edit an Accepted decision; supersede it with a new ADR.
  - Use British spelling, plain language, no emoji, and no invented facts (unknowns go under `## Open`).
- When adding CI for a new service, add a separate job per service instead of extending the `site` build job.
