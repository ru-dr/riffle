# 0007: Add repositories to the training corpus

- **Date:** 2026-10-03
- **Status:** Proposed
- **Owners:** #5 Data ingestion

## Context

The corpus is built in versions: v1 has 50 repositories in 5 categories, and v2 adds 50 more in 6 new categories. Across 98 measured v2 repositories there are 2,919,190 PRs. Pilot mining found very few bad outcomes in well-gated libraries. Fast-moving application and framework repositories that merge through GitHub are expected to give more positives. Nine repositories in the first draft of this ADR are already in v1 or v2.

## Decision

- Create corpus v3: v2 plus the 20 repositories below.
- Add them to the miner's repository list, `services/miner/repos.txt`, the location named in `contracts/mined_history.schema.json`.
- **Application monorepos:** `getsentry/sentry`, `PostHog/posthog`, `supabase/supabase`, `calcom/cal.com`, `n8n-io/n8n`, `mattermost/mattermost`, `discourse/discourse`, `apache/superset`, `airbytehq/airbyte`.
- **Frameworks and tools:** `vitejs/vite`, `denoland/deno`, `astral-sh/ruff`, `astral-sh/uv`, `tauri-apps/tauri`.
- **ML and data:** `pandas-dev/pandas`, `pydantic/pydantic`.
- **Infrastructure:** `argoproj/argo-cd`, `traefik/traefik`, `etcd-io/etcd`, `keycloak/keycloak`.
- Not re-added, already in the corpus:
  - v1: `vercel/next.js`, `apache/airflow`, `scikit-learn/scikit-learn`, `prometheus/prometheus`, `hashicorp/terraform`.
  - v2: `ray-project/ray`, `vllm-project/vllm`, `sveltejs/svelte`, `mastodon/mastodon`.
- Each repository passes an admission check before extraction: most merged PRs land through GitHub (`merge_via = github_merge`, ADR 0008), it has enough merged PRs, and its CI is readable as GitHub checks or commit statuses. Thresholds are under Open.
- All v3 additions go to the training split. The v2 unseen validation and final test sets stay unchanged.
- PR counts are measured with the GitHub API before extraction and recorded in the dataset plan.

## Options not taken

| Option | Why not |
| --- | --- |
| Keep only the v2 corpus | Too few positives expected in well-gated libraries |
| Reweight or oversample existing positives | Adds no new failure patterns |
| Add SZZ to the training target | Lowers label precision; ADR 0002 keeps SZZ as an ablation |
| Spread v3 repositories across all splits | Changes the held-out sets and breaks comparison with v2 results |

## Consequences

- About 20 more repositories to mine, with more API time, clone space, and extraction time.
- CI history for the new repositories covers only the last 90 days, because GitHub caps check and status retention on public repositories. Older `ci_fail` is unobserved and handled by `labels.unobserved` in `contracts/org_rules.schema.json`.
- Choosing repositories because they fail more raises the training base rate. Calibration is checked on the unchanged v2 validation set, and metrics are reported per repository.
- `docs/` dataset plan and the scoping document list v3 as its own row.
- No conflict with the invariants. Data comes from public repositories, and features stay point-in-time.

## Revisit if

- The v3 positive rate is not higher than v2 after the first extraction.
- More than 5 of the 20 repositories fail the admission check.
- Calibration on the v2 validation set gets worse after adding v3.

## Open

- Pilot positive rate for `label_bad`, and the target rate for v3.
- Admission thresholds. Suggested: at least 90% of merged PRs via `github_merge`, and at least 1,000 merged PRs over the last 12 months.
- PR counts for the 20 repositories.
- Confirm none of the 20 are already in the v2 list.
- Miner location: `services/miner/` in the schema, but the repository tree has no miner folder yet.