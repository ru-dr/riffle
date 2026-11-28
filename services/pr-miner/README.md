# pr-miner (Python)

Turns each PR event into the row the model was trained on: a row of
`contracts/mined_dataset.schema.json`, made by the repo miner's own code, published as a
`PrFeatures` message (`contracts/pr_features.schema.json`) for the scorer. ADR 0027.

> Not built yet. It needs three changes in the miner first (below).

```
pr-events ─► pr-miner ─► pr-features ─► scorer
              │
              ├─ git fetch the repo copy (only what is new)
              ├─ mine_pr(PR) with the miner's code: LLM, Semgrep and OSV off
              └─ publish { delivery_id, tenant_id, repo, pr_number, head_sha, action,
                           miner_version, mined_at, disabled_sources, row }
```

Invariants:
- **The row is made by the miner's code, never re-implemented here.** Training and serving then
  agree by construction.
- **`miner_version` is the version the model in service was trained with.**
- **The LLM is never called**, and no diff leaves the installation.
- **An open PR's outcome and label fields are null.**
- Idempotent on `delivery_id`: the same event makes the same row.

| Example | File |
| --- | --- |
| A complete message (PR #42 of the fixture installation, from a real mined row) | `fixtures/pr_features/pull_request.opened.json` |

## What the miner needs first (`pipelines/ingestion/repo_miner`)

| Change | Why |
| --- | --- |
| Log in as the GitHub App (installation token per tenant) | It uses personal tokens today |
| Reuse a saved clone and history index | It rebuilds them per run, which takes hours on a big repo |
| Callable as a library (`mine_pr()` with the saved setup) | It is a command-line batch tool today |

## Deploy (when built)

With the shared chart (`infra/k8s/charts/riffle-service`), namespace `riffle-workers`, priority
`riffle-workers`, KEDA on `SUBSCRIPTION_PR_MINER_PR_EVENTS`. It runs as the `pr-miner` service
account (`infra/k8s/platform/identity`), reads `pr-miner-secrets` (the GitHub App key) and may use
the `snapshots` bucket (repo copies, history store) and the tenant key
(`infra/terraform/envs/staging/09-identity.tf`).
