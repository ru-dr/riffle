# Riffle — repo data extractor

Extracts the **v1** Riffle inference features from a **cloned GitHub repo**, one
row per merged PR, computed **as of each PR's base commit** (point-in-time, no
leakage). Excludes Source 1 (`riffle.yml`) by design — those are team-config
features supplied elsewhere.

## What it produces

One table (`.parquet` or `.jsonl`), one row per merged PR, with:

| Source | Module | Examples |
|---|---|---|
| 2 — repo history | `features/history.py` | revert/fix density, churn, hotspots, ownership, coupling, author behavior, repo baselines |
| 3 — line-level blame | `features/line_history.py` | `hunk_fix_proximity`, `mod_line_age_days`, `mod_lines_self_authored_share` |
| 4a/4b — diff & paths | `features/diff_stats.py` | Kamei LA/LD/NF/ND/NS/Entropy, migration/lockfile/CI/auth path flags |
| 4c — PR metadata | `mine_repo.py` (`_pr_metadata`) | draft, author_association, bot/agent, labels, milestone |
| 4d — scanners | `features/scanners.py` | lizard `ccn_delta`, semgrep new findings |
| 4e — deps/CVE | `features/deps_cve.py` | OSV + GHSA introduced vulns, major bumps (npm, PyPI, Go, Maven, Ruby, Rust, PHP, NuGet, Dart, Elixir) |
| 5 — deterministic | `features/deterministic.py` | secrets, env keys, off-by-one edits, migration reversibility |
| 6 — LLM flags | `features/llm_flags.py` | authz change, error-handling removed, semantic risk (opt-in) |
| Labels | `labels.py` | `label_revert`, `label_ci_fail`, `label_ci_fail_optional`, `label_hotfix`, `label_szz_bug`, `label_bad` (+ `mined_at`) |

Per-file features are collapsed to fixed `max_/mean_/sum_/min_` columns by
`aggregate.py`, so every PR yields the same schema regardless of how many files
it touches.

## Design guarantees

- **Point-in-time.** All history/blame features walk commits strictly *before*
  the base commit's timestamp. Nothing from after the PR opened is used.
- **Deterministic-first.** Parsers/regex/linters are preferred over the LLM
  wherever an exact answer exists (secrets, off-by-one, migrations).
- **Graceful degradation.** If a tool (lizard, semgrep) or the network is
  unavailable, the affected features come back `None` (treated as *missing* by
  XGBoost/LightGBM) rather than a misleading `0`.
- **No `riffle.yml`.** Source 1 is intentionally out of scope.

## Install

```bash
pip install -r requirements.txt      # lizard, pandas/pyarrow, openai, semgrep
```

Only `git` and the Python standard library are required for the core; everything
else is an optional stage. Put your tokens in a `.env` file at the package root
(auto-loaded at import — no need to export them):

```bash
# .env
GITHUB_TOKEN=ghp_...                 # PR list, CI checks, labels
LLM_API_KEY=sk-ant-...               # only needed with --llm
LLM_MODEL=claude-sonnet-5            # optional override
LLM_BASE_URL=https://api.anthropic.com/v1/   # optional override
```

See `.env.example`. `.env`, `cloned_repos/`, `ci_cache/`, and `*.parquet` are
git-ignored.

## Use (one repo)

```bash
# clone the target repo first (full history — do NOT use --depth)
git clone https://github.com/ORG/REPO cloned_repos/ORG_REPO

python mine_repo.py ^
  --repo cloned_repos/ORG_REPO ^
  --out ORG_REPO.parquet ^
  --max-prs 500
```

`--owner`/`--name` are auto-detected from the clone's git remote, so you usually
only need `--repo`. `--out` defaults to `<owner>_<name>.parquet`. PRs are
selected most-recently-**merged** first.

```text
flags:
  --max-prs N        cap at the N most-recently-merged PRs (omit = all)
  --append           append rows to --out and dedup by (repo, pr_number);
                     lets one file accumulate across many runs
  --llm              enable Source 6 LLM semantic flags (needs LLM_API_KEY; costs $/PR)
  --ci-history       add historical CI-fail-rate features (slow API pre-pass, cached)
  --szz              add the label_szz_bug fix-tracing label (slow blame pre-pass, cached)
  --no-network       git-only: no PR list, no labels, no OSV (features only)
  --no-scanners      skip lizard + semgrep
  --no-api-contract  skip oasdiff / buf / graphql-inspector spec diffing
```

Output is one row per merged PR. Each row records `mined_at`; PRs merged too
recently may not have their outcome yet, so decide maturity at training time
(see the snippet under *Build a dataset*).
`--ci-history`, `--szz`, and `--llm` are off by default; their columns come back
`null`/`False` until enabled.

## Build a dataset (many repos)

`build_dataset.py` clones every repo listed in `repos.txt` (one GitHub URL per
line; `#` comments allowed) and mines them all into **one growing parquet**,
de-duplicated by `(repo, pr_number)`.

```bash
# quick smoke test — confirms the clone -> mine -> append loop works
python build_dataset.py --max-prs 15

# real dataset run — git features + SZZ + CI-history, no LLM yet
python build_dataset.py --max-prs 1500 --szz --ci-history

# add LLM only once an ablation shows the flags earn their cost
python build_dataset.py --max-prs 1500 --szz --ci-history --llm
```

```text
flags (passed through to the miner):
  --repos-file F     list of GitHub URLs (default repos.txt)
  --out F            single dataset file, rows appended (default dataset.parquet)
  --clone-dir D      where repos are cloned (default cloned_repos)
  --max-prs N        cap PRs per repo
  --szz              add the SZZ fix-forward label
  --ci-history       add historical CI-fail-rate features
  --llm              enable LLM semantic flags
  --skip-existing    reuse an already-cloned repo instead of re-cloning
```

Notes: repos are **full clones** (the extractor needs complete history). A small
`--max-prs` selects the newest — and therefore most *immature* — PRs; use a large
cap to reach back into mature history where real positives live. After a run:

```python
import pandas as pd
df = pd.read_parquet("dataset.parquet")
age = (pd.to_datetime(df.mined_at) - pd.to_datetime(df.merged_at)).dt.days
N = 30                                                    # maturity threshold (days)
m = df[df.label_bad | (age >= N)]                         # outcome known: positive, or old enough
print("trainable:", len(m), "| label_bad:", int(m.label_bad.sum()),
      "| szz:", int(m.label_szz_bug.sum()),
      "| fix-forward recovered:", int((m.label_szz_bug & ~m.label_bad).sum()))
```

## Labels

Six label columns, filled from outcomes after merge (not inference inputs):

- `label_revert` / `label_ci_fail` / `label_hotfix` — the three explicit
  outcomes. `label_ci_fail` counts only the **required/gating build** check;
  auxiliary checks (CodeQL, Scorecards, coverage) go to `label_ci_fail_optional`
  (diagnostic only).
- `label_bad` — the v1 training target: `revert OR ci_fail OR hotfix`. **SZZ is
  not included.**
- `label_szz_bug` — SZZ fix-tracing (`--szz`): this PR introduced a line a later
  fix commit repaired. Catches fix-forward defects the explicit labels miss;
  noisier, so kept separate. Union it into the target yourself at training time
  if wanted: `y = label_bad | label_szz_bug`.
- `mined_at` — when the row was extracted (not a label or a feature). Maturity
  is computed from it at training time: keep a row if `label_bad` is true, or if
  `mined_at - merged_at` is at least your chosen threshold (e.g. 30 days, longer
  for SZZ).

## What still needs your environment

`features/llm_flags._call_llm` is provider-specific — the default targets Claude
via Anthropic's OpenAI-compatible endpoint (`--llm` + `LLM_API_KEY`). Swap model
or provider with `LLM_MODEL` / `LLM_BASE_URL` in `.env`. `api_contract_change`
shells out to `oasdiff` / `buf` / `graphql-inspector`; install whichever spec
formats you care about (each degrades to `None` if absent).

## Validate before scaling

Run on one repo that also appears in ApacheJIT and check that your Kamei metrics
(NF, Entropy, NDEV, …) roughly match the published values for overlapping
commits. If they do, the miner is correct; then scale to ~30 repos.

## Module map

```
config.py        path rules, tunables, stage toggles
gitio.py         point-in-time git helpers (log/blame/show/diff at base SHA)
github_api.py    PR list, CI check conclusions, labels (rate-limit aware)
labels.py        revert / CI-fail / hotfix labels + maturity cutoff
aggregate.py     per-file dicts -> fixed max/mean/sum/min columns
features/
  diff_stats.py    Source 4a/4b + per-PR Kamei metrics
  history.py       Source 2 (as-of-base index, per-file, author, coupling)
  line_history.py  Source 3 (git-blame of modified lines)
  scanners.py      Source 4d (lizard, semgrep)
  deps_cve.py      Source 4e (OSV, GHSA)
  deterministic.py Source 5 (regex/parser checks)
  llm_flags.py     Source 6 (schema-constrained LLM, opt-in)
mine_repo.py     orchestrator + CLI (one repo)
build_dataset.py batch driver: clone repos.txt + mine all into one dataset
```
