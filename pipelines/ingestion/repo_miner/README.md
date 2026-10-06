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

## Splitting, waiting, and resuming

- `repos.txt` holds the full 100-repo corpus (v1 #1-50, v2 #51-100) from
  `site/content/docs/dataset-plan.md`, each tagged `[~N commits]`.
- Several machines: `--shard K/N` makes this machine PC K of N. Each PC gets
  an equal number of repos (±1) with balanced total size: heaviest repos
  first, each to the lightest PC that still has room. With a fixed
  `--max-prs` the API/LLM cost per repo is equal, so commit count is what
  differs (clone, history walk, SZZ). The split is deterministic, so every
  PC computes the same one. `--shard-plan N` prints it. In the TUI: set
  "Number of PCs" and "This PC #", and "Show split" (ctrl+g) lists them.
  GitHub rate limits are per account, so use a token from a different
  account on each PC.
- `--min-age-days N` (TUI: "Skip PRs merged in last N days", default 90)
  skips PRs merged in the last N days, so `--max-prs` picks PRs whose labels
  are mature. Busy repos merge 100 PRs in days, so without it most rows would
  be too young to train on. 90 covers the 30-day maturity and 90-day SZZ
  windows. Costs extra PR-list pages on busy repos.
- Rate limits: GitHub calls sleep until the quota resets (or `Retry-After`)
  and retry; 5xx/network errors back off and retry, then count as unobserved.
- Stop any time (ctrl+c / TUI Stop). Finished PRs are in `<out>.partial.jsonl`
  and finished repos are in `--out`; rerunning the same command (keep
  `--skip-existing`) skips both and continues. `--remine` redoes every repo.

## Repo-local mined features

The nine columns of `contracts/mined_features.schema.json`
(`features/repo_local.py`): `similar_pr_bad_rate`, `similar_pr_count`,
`revealed_path_scrutiny`, `path_failure_mode_revert` / `_ci` / `_hotfix`,
`path_detection_lag_days`, `path_size_pctile`, `release_cycle_position`.

- `path_size_pctile` is computed per PR from git history at the base commit.
  The PR-history columns are filled once a repo's PRs are all mined.
- Point-in-time: for a PR opened at T, only past PRs merged before T and at
  least `maturity_days` (30) old at T count, and only outcome events that
  happened before T. Only reviews submitted before T count.
- `revealed_path_scrutiny` needs review events (2 API calls per PR, so
  `GITHUB_TOKEN`); `--no-review-history` skips them and leaves it null.
- History is the PRs mined in the same run, so with `--max-prs N` the oldest
  rows have little history and come back null. Thresholds mirror the
  `rules.mining` defaults (Settings in `config.py`).
- The contract's `evidence` block is for the narrator at scoring time and is
  not written to the training dataset.

## TUI

```bash
python tui.py
```

Every launch first runs the environment doctor (`doctor.py`). It checks
Python, git, the Python packages, `GITHUB_TOKEN` (verified against GitHub, so
the `.env.example` placeholder doesn't count), the LLM setup, optional
API-diff tools, disk and RAM, and offers fixes: it pip-installs missing
packages, asks for a token and saves it to `.env` (private, gitignored), and
offers the install command for git / Claude Code. Run it on its own with
`python doctor.py`, or `python doctor.py --check` to only report (exit 1 on a
failure). `RIFFLE_NO_DOCTOR=1` skips it.

On an externally managed Python (Ubuntu/Debian 23.04+, PEP 668) packages go
into `repo_miner/.venv`, created on first need; later launches re-run inside
it automatically. If `python3-venv` is missing, the doctor offers the `apt`
command.

The TUI log is selectable (drag, then ctrl+c); ctrl+y copies the whole log and
ctrl+b shows a QR code of the web view's link to scan with a phone (o opens
it here, c copies the link). The web view (read-only, LAN,
random key per launch) has log search, level filter, wrap / follow / pause /
expand, copy and download, and keyboard shortcuts (`?` lists them).

LLM usage: exact token counts per run (in, cache read / write, out) and the
API-price equivalent of those tokens, plus the Claude plan's current-session
usage and reset countdown from Claude Code's own `/usage` (run headless; no
tokens or quota). `RIFFLE_NO_PLAN_USAGE=1` turns the `/usage` poll off.

Re-mining a repo: `python build_dataset.py --audit` (or Audit, ctrl+k, in the
TUI) reports each repo's blame coverage and flags repos mined while blame was
failing (before v1.6.0, repos that ship `.git-blame-ignore-revs`).
`--remine-repo OWNER/NAME` (the TUI's "Re-mine repos" field, filled by Audit)
drops that repo's rows, SZZ cache and checkpoint and mines it again. Its
earlier LLM answers are kept and reused for PRs with the same commits, so the
re-mine makes no new LLM calls for them.
Coverage counts only PRs that touch files that already existed (a PR that only
adds files has no history to blame): 95-100% is healthy, `<- check` (50-95%)
means look for `[blame] warning` in `logs/run-*.log`, `<- re-mine` (under
50%) means blame was failing. A re-mine only runs for repos in this PC's share
(`--shard`); naming another PC's repo stops with nothing changed.
`--auto-remine` (TUI: "Auto re-mine broken repos") runs the audit at Start and
re-mines the flagged repos in this PC's share automatically.

Configure the batch on the left (repos file, output, PR cap, workers, LLM /
SZZ / CI toggles), press **ctrl+r** to start. The right side shows per-repo
progress, PR/min, ETA, LLM ok/fail + latency, circuit-breaker state, and the
log. **ctrl+x** stops (SIGINT); the checkpoint keeps finished PRs, so starting
again with "Reuse clones" on resumes.

## LLM backend and performance

- `--llm` defaults to **headless Claude Code** (`claude -p`, your Claude Code
  login, no API key; model `sonnet`, override with `LLM_MODEL`).
  `LLM_BACKEND=api` uses the OpenAI-compatible endpoint + `LLM_API_KEY`.
- **Circuit breaker:** after `LLM_BREAKER_THRESHOLD` (default 5) consecutive
  LLM failures the miner stops, drops the rows from the failing streak, leaves
  `--out` untouched, and exits 3; `build_dataset.py` aborts the batch. Rerun
  to resume from the checkpoint.
- **Parallel PRs:** `--workers N` (default `min(6, CPUs)`) mines PRs in worker
  threads, consumed in PR order (output identical to a sequential run).
  `--llm-concurrency N` (default 4) caps simultaneous LLM calls.
- The repo's `git log --numstat` is parsed once and reused per PR, hotspot
  complexity is cached per blob, and path classification is memoized.

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
