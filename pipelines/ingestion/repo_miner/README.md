# Riffle — repo data extractor

> Full reference (architecture, how a run works, every column, every flag, why each choice): [`ARCHITECTURE.md`](ARCHITECTURE.md).

Extracts the **v1** Riffle inference features from a **cloned GitHub repo**, one
row per closed PR (merged or not), computed **as of each PR's base commit** (point-in-time, no
leakage). Excludes Source 1 (`riffle.yml`) by design — those are team-config
features supplied elsewhere.

## What it produces

The **dataset** table (`.parquet` or `.jsonl`), one row per closed PR (merged, and since
v2.0.0 closed-unmerged too; see *Merge-outcome labels*), with:

| Source | Module | Examples |
|---|---|---|
| 2 — repo history | `features/history.py` | revert/fix density, churn, hotspots, ownership, coupling, author behavior, repo baselines |
| 3 — line-level blame | `features/line_history.py` | `hunk_fix_proximity`, `mod_line_age_days`, `mod_lines_self_authored_share` |
| 4a/4b — diff & paths | `features/diff_stats.py` | Kamei LA/LD/NF/ND/NS/Entropy, migration/lockfile/CI/auth path flags |
| 4c — PR metadata | `mine_repo.py` (`_pr_metadata`) | draft, author_association, bot/agent, labels, milestone |
| 4d — scanners | `features/scanners.py` | lizard `ccn_delta`, semgrep new findings |
| 4e — deps/CVE | `features/deps_cve.py` | OSV + GHSA introduced vulns, major bumps (npm, PyPI, Go, Maven, Ruby, Rust, PHP, NuGet, Dart, Elixir) |
| 5 — deterministic | `features/deterministic.py` | secrets, env keys, off-by-one edits, migration reversibility |
| 6 — LLM flags | `features/llm_flags.py` | authz change, error-handling removed, semantic risk (on by default; `--no-llm` turns it off) |
| Labels | `labels.py` | `label_revert`, `label_ci_fail`, `label_ci_fail_optional`, `label_hotfix`, `label_szz_bug`, `label_bad` (+ `mined_at`) |
| Merge-outcome labels | `merge_labels.py` | `label_merged`, `label_rework`, `close_reason` and its flags (default on; `--no-merge-outcome` drops them) |

Per-file features are collapsed to fixed `max_/mean_/sum_/min_` columns by
`aggregate.py`, so every PR yields the same schema regardless of how many files
it touches.

## Design guarantees

- **Point-in-time.** Diff features come from the PR as it stood when it
  opened (ADR 0010; `open_snapshot` says how that state was found), and
  history/blame features walk commits strictly *before* its fork point.
  Nothing from after the PR opened is used.
- **Deterministic-first.** Parsers/regex/linters are preferred over the LLM
  wherever an exact answer exists (secrets, off-by-one, migrations).
- **Graceful degradation.** If a tool (lizard, semgrep) or the network is
  unavailable, the affected features come back `None` (treated as *missing* by
  XGBoost/LightGBM) rather than a misleading `0`.
- **No `riffle.yml`.** Source 1 is intentionally out of scope.
- **Contract.** Every row has exactly the columns of
  `contracts/mined_dataset.schema.json`, whichever stages are on: a stage
  that is off leaves its columns `null`, never missing.
  `tests/test_dataset_contract.py` mines a small repo with stages on and off
  and fails on any column the contract doesn't list, any contract column the
  miner didn't write, a row that doesn't validate, or a row that doesn't
  round-trip through the flat form (ADR 0012). Adding a column means adding
  it to the contract in the same PR.

## Upgrading to 7.0.0 (re-mine needed)

7.0.0 is the audit-fix release. It changes the meaning of columns that already exist, so a dataset mined with 6.x
should be mined again before it is used to retrain (`merge_shards.py` refuses a mix of versions unless told otherwise).
What changed, in short; section 21 of [`ARCHITECTURE.md`](ARCHITECTURE.md) has the detail:

- **Labels.** `label_revert` and `label_hotfix` no longer match the PR's own merge commit or commits. A revert must
  carry `#N` in its subject or `Reverts owner/repo#N` in its body, and is searched from the default-branch tip.
  `label_szz_bug` and `days_to_szz_fix` are `null` when SZZ did not run, not `false`. SZZ follows renames (`SZZ_VERSION`
  3, so cached SZZ indexes rebuild).
- **Git and history features.** Paths with non-ASCII, quoted or backslash characters now get their lines of code, blame
  and SZZ. Renamed files are looked up under their old name at the base. Merge commits and bots no longer count as
  authors or owners.
- **Dependencies, CVEs and scanners.** CVE columns are as of the PR's open time (records published later or since
  withdrawn are dropped). A scan, a parse or a lookup that did not run or failed is `null`, never `0`.
- **LLM columns.** `llm_prompt_version` now covers the diff rules, so it changes for every dataset. A diff with no code
  left after slimming is status `empty`, with null flags and no call.
- **Snapshots** can be re-assembled from `snapshots_raw/` with `python snapshots.py --dataset <dataset>
  --raw-root snapshots_raw`; the dismissed-review fix needs the PR captured again.
- **Training** (`pipelines/training/frames.py`) masks all 125 diff-derived columns on `open_snapshot == final` rows and
  groups the split by repo, PR and shared head commit, so splits for mixed-case repo names change.

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
LLM_BACKEND=claude_cli               # default: headless Claude Code on your own login, no key
LLM_MODEL=sonnet                     # default for claude_cli (claude-sonnet-5 with the api backend)
LLM_API_KEY=sk-ant-...               # only with LLM_BACKEND=api
LLM_BASE_URL=https://api.anthropic.com/v1/   # api backend only
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
flags (the best setup is the default; each stage has a --no- form):
  --max-prs N        cap at the N most-recently-merged PRs (omit = all)
  --min-age-days N   skip PRs merged in the last N days (default 90; 0 = off)
  --append           append rows to --out and dedup by (repo, pr_number);
                     lets one file accumulate across many runs
  --llm              LLM semantic flags + close-reason classifier (default on;
                     the run stops if no backend is usable; --no-llm to skip on purpose)
  --ci-history       historical CI-fail-rate features (<=400 API calls, cached; on)
  --szz              label_szz_bug fix-tracing label (windowed, cached; on)
  --declared-labels  declared_hotfix, labels at open (from the timeline; on)
  --merge-outcome    closed-unmerged + stale-open PRs and merge labels (on)
  --max-unmerged N   cap unmerged rows per repo (default: the repo's real mix)
  --no-network       git-only: no PR list, no labels, no OSV (features only)
  --no-scanners      skip lizard + semgrep
  --no-api-contract  skip oasdiff / buf / graphql-inspector spec diffing
  --llm-model M      model for the LLM flags (default LLM_MODEL, else sonnet)
  --llm-test         one call on a sample diff, report, exit
  --pr-discovery {api,git}   how merged PRs are enumerated (default api)
  --domain NAME      dataset domain written on every row (ADR 0011)
```

Output is one row per closed PR (merged only with `--no-merge-outcome`). Each row records `mined_at`; PRs merged too
recently may not have their outcome yet, so decide maturity at training time
(see the snippet under *Build a dataset*).
Every stage is on by default; `--no-llm`, `--no-szz`, `--no-ci-history`,
`--no-declared-labels` and `--no-merge-outcome` turn one off, and its columns
then come back `null`.

## Selecting PRs: date window (v4.0.0)

For the dataset runs, select by date instead of "the newest N merged PRs":

```bash
python build_dataset.py --window-from 2023-07-01 --window-to 2026-07-31 \
    --max-prs 8000 --only-domain web-ts
```

- Every closed PR **created** in the window, merged and unmerged together, so
  every repo covers the same span and keeps its real merged / unmerged mix.
  Drafts are left out, and so are PRs whose outcome is younger than
  `--min-age-days`.
- `--max-prs` then keeps the **newest contiguous block** of that many PRs (by
  creation time).
- **History lead-in:** PRs created in the `--lead-in-days` (default 180)
  before the block are read as metadata only (the PR list plus one timeline
  call each). They are never mined into rows; they give the block's first PRs
  their merge-signal history (`repo_outside_merge_rate`, first-response
  times, agent load, similar PRs). The breakage history (`similar_pr_*`,
  `path_failure_mode_*`) needs mined outcomes, so it still starts empty.
- `--cap-on training` makes `--max-prs` count only **training PRs** (outside
  contributors who aren't bots, plus the 5% sample; `scope.py`), so
  `--max-prs 10000 --cap-on training` gives about 10,000 PRs to train on.
  Maintainer and bot PRs inside the block are still mined (history needs
  them) but don't count. Maintainers confirmed from history are only known
  after the timelines are read, so the training count can come out slightly
  below the cap. The `[window]` line says how many training PRs it kept.
- Without `--window-from` the older selection applies.
- **`--full-repo`** mines the repo's whole history: every closed PR ever
  opened, merged and unmerged, with no date window, no `--max-prs` cap and no
  lead-in. It is a window from 2000-01-01 to 9999-12-31, so everything above
  about drafts and `--min-age-days` still holds. It can't be combined with
  `--window-from/--window-to`, `--max-prs`, `--lead-in-days`, `--cap-on` or
  `--test`. Expect long runs on big repos; use `--shard` to split them.

**Domains (ADR 0011):** tag a line in the repos file with `[domain: web-ts]` in its `#` comment (`https://github.com/o/r  # [~9000 commits] [domain: web-ts]`);
`--only-domain web-ts` mines just those repos, and every row records its
`domain`.

**Provenance:** every row records `miner_version`, `miner_commit` (with
`-dirty` when the miner had uncommitted changes), `llm_model` and
`llm_prompt_version` (a hash of the prompts and schemas), so rows from
different miner versions or LLM setups can be told apart.

**Bots and agents:** `author_is_bot` is automation only (Dependabot,
Renovate, github-actions, other `[bot]` accounts); `ai_agent_authored` is AI
coding agents only, from the same signals as `agent_name` (account, branch
prefix, commit trailer, body boilerplate). An agent PR is never a bot PR, so
agent PRs stay in `--llm-scope training`.

**Health check:** after each repo the miner prints a summary (rows, merged,
outside and unmerged-outside shares, bots, agents) and flags repos that look
wrong: under 50 rows, almost no unmerged outside PRs (PRs may land outside the
merge button), over half bot PRs, LLM columns missing on in-scope rows, or CI
unobserved on most merged rows. Saved as `health/<owner>_<name>.json`
(`--health-dir`).

## Before the big run: tools and the night-1 pilot

- **`repos_web_ts.txt`:** the first domain (ADR 0011), 43 JavaScript /
  TypeScript repos tagged `[domain: web-ts]`, `[monorepo]` on the app
  monorepos, with what was left out and why at the top.
- **`scout.py`** ranks candidate repos without cloning: outside-contributor
  share, unmerged share among outside PRs, bots, agents, from each repo's 100
  latest closed PRs (`--agent-search` adds agent PR counts for 12 months).
  Caveat: GitHub reports org members with private membership as
  `CONTRIBUTOR`, so company-run repos can look more "outside" than they are.
- **`llm_compare.py`** asks two models (default sonnet and haiku) about the
  same ~300 diffs, exactly as the miner asks, and reports per-flag agreement,
  so one model can be chosen for the whole dataset. `--dry-run` shows the
  calls first; answers are saved as they arrive, so it resumes.
- **`audit_export.py`** writes a stratified ~600-PR CSV (equal per outcome
  class, agent PRs over-weighted) with links and the miner's labels, and
  blank columns for the human check of label precision.

**Night-1 pilot** (one full repo, before scaling):

```bash
python build_dataset.py --repos-file repos_web_ts.txt --only-domain web-ts \
    --window-from 2023-07-01 --window-to 2026-07-31 --max-prs 8000 \
    --remine-repo vitejs/vite      # or a repos file with just vite
python snapshots.py --dataset dataset.parquet --raw-root snapshots_raw
```

Record from the log, `health/vitejs_vite.json` and the snapshot output:
seconds per row, LLM calls and `$ at API prices` per row, whether the plan
pause triggered, head commits per captured PR, `snapshots_raw/` size, and the
health flags. Scale the estimates for the full run from those.

## Splitting, waiting, and resuming

- `repos.txt` holds the full 100-repo corpus (v1 #1-50, v2 #51-100) from
  `site/content/docs/dataset-plan.md`, each tagged `[~N commits]`.
- Several machines: `--shard K/N` makes this machine PC K of N. Each PC gets
  an equal number of repos (±1) with balanced total size: heaviest repos
  first, each to the lightest PC that still has room. With a fixed
  `--max-prs` the API/LLM cost per repo is equal, so commit count is what
  differs (clone, history walk, SZZ). The split is deterministic, so every
  PC computes the same one. `--split-by commits|prs` (default `commits`) chooses what it balances: the `[~N commits]`
  tags, or the `[N PRs]` tags. Use `prs` for a `--full-repo` run, where the per-PR work (API calls, features, LLM)
  dominates; on the 43 web-ts repos over 6 PCs it cuts the busiest-to-lightest PC from 2.2x to 1.04x. Every PC must use
  the same value: it travels in the join code and is locked in the TUI ("Split repos between PCs by"), and it changes the
  split ID. `--shard-plan N` prints it. In the TUI: set
  "Number of PCs" and "This PC #", and "Show split" (ctrl+g) lists them, with
  each repo's commits and PR count (from its `[N PRs]` tag) and a PR total per PC.
  GitHub rate limits are per account, so use a token from a different
  account on each PC.
- Output names: a `--shard` run tags its dataset automatically, so
  `--out dataset.parquet` on PC 1 of 3 writes `dataset_pc1of3.parquet` (and its
  run log is `logs/run-<time>-pc1of3.log`). `--out-prefix web` / `--out-postfix
  lab` add your own text (placeholders `{pc}` `{n}` `{host}` `{date}`; a postfix
  replaces the automatic tag); `--no-auto-name` turns the tag off. A name that
  already carries its tag is left alone, so a rerun resumes the same file. If
  an untagged `dataset.parquet` already exists, the run says so, because
  `--skip-existing` only looks at the tagged file.
- Putting the PCs' files together: `python merge_shards.py --out dataset.parquet`
  reads each PC's `dataset_pcKofN.shard.json` (written when its batch ends) and
  merges only if every PC 1..N is there, all used the same split ID and miner
  version (`--allow-version-mix` to override), no repo is on two PCs, no row repeats
  and the snapshots still match their dataset (`consistency.py`). It writes
  `dataset_merged`, `dataset_merged_snapshots`, `dataset_merged_commit_features`
  and `dataset_merged.merge.json`. The three tables stay three: they have one row
  per PR, per snapshot and per head commit, and are joined when training loads
  them (`pipelines/training/frames.py`), not stored merged.
- Tokens in one place: `python doctor.py` checks the GitHub token, the join token (and offers to paste the join code), the read token (saved as `RIFFLE_READ_TOKEN`; the TUI's F4 copies it and opens the store) and, for the lead, the admin token.
- Joining a split (ADR 0015): the lead plans the split with
  `python build_dataset.py <the flags every PC should run> --shard-plan N --issue-tokens [--token-days D]`
  (needs `RIFFLE_SYNC_URL` and `RIFFLE_ADMIN_TOKEN` in the lead's `.env`). The store mints one
  write token per PC, bound to that split, and the plan prints one **join code** per PC. Send each
  code only to its PC's owner. They run `python join.py CODE`: it checks the code with the store,
  writes `RIFFLE_SYNC_URL` and `RIFFLE_SYNC_TOKEN` into `.env` (mode 600) and prints the exact
  command to run, which is the lead's flags plus `--shard K/N`. `python join.py --check` and
  `python doctor.py` report whether the token still works. A token writes only under its own split,
  so if a PC's flags make a different split the run turns sync off and says why. Running the plan
  again for the same split issues new tokens and revokes the old ones.
- Managing who may upload (lead only, needs `RIFFLE_ADMIN_TOKEN`): `python admin.py list` shows every PC token with its
  split, expiry and last upload; `python admin.py revoke NAME` and `revoke-split ID` cut access at once;
  `python admin.py whoami` checks the admin token. The admin token reads no data itself, but it can add readers, and a reader's token reads. The store also has an admin web page (`/admin`) with the same powers plus rotating a split and adding readers; sign in there with the admin token.
- Central sync (ADR 0015): with `RIFFLE_SYNC_URL` and `RIFFLE_SYNC_TOKEN` in
  `.env`, the miner sends its logs, traces, `health/*.json`, dataset, snapshot and
  commit-feature tables, `.partial.jsonl` checkpoint and shard manifest to the
  team store after each repo, on a Stop and at the end of a batch (`--no-sync`
  turns it off). It is incremental and never fails a run; a store that cannot be
  reached is a warning, and a Stop waits at most `RIFFLE_SYNC_TIMEOUT` seconds
  (default 120). `ci_cache/`, `snapshots_raw/` and the clones are never sent: they
  are what a resume reads, and a resume happens on the same PC.
- Run trace: next to each run log, `logs/run-*.trace.jsonl` holds one JSON line
  per event (`runlog.py`): each run stage with its duration, one line per PR with
  its time split by stage (diff, history, git, api, llm, quota_wait, ...), GitHub
  retries and rate-limit waits, slow git/API/LLM calls, and a heartbeat every 2
  minutes that names what each worker is doing (WARN if no PR finished for 5).
  `--log-level DEBUG` adds every git, API and LLM call. The run log also gets
  `[stage] X done in 2m57s` lines. `python trace_report.py logs/run-*.trace.jsonl`
  summarizes a run: stage times, slowest PRs, stalls, warnings, quiet gaps.
- `--min-age-days N` (TUI: "Skip PRs merged in last N days", default 90)
  skips PRs merged in the last N days, so `--max-prs` picks PRs whose labels
  are mature. Busy repos merge 100 PRs in days, so without it most rows would
  be too young to train on. 90 covers the 30-day maturity and 90-day SZZ
  windows. Costs extra PR-list pages on busy repos.
- Rate limits: GitHub calls sleep until the quota resets (`Retry-After` as seconds or a date, capped; one gate
  pauses every thread) and retry; 5xx/network errors back off and retry, then count as unobserved.
- Stop any time (ctrl+c / TUI Stop). Finished PRs are in `<out>.partial.jsonl`
  and finished repos are in `--out`; rerunning the same command (keep
  `--skip-existing`) skips both and continues. `--remine` redoes every repo.
- **Resume picks up exactly where it stopped (v5.1.0).** The first run of a
  repo pins its PR selection (`ci_cache/<owner>_<name>.selection.json`: the
  merged, unmerged, stale-open and lead-in PRs, and the maintainer evidence),
  so a resume mines the same PRs even if more merged since, and skips the PR
  list, lead-in and timeline fetches. Removed after a clean write and by
  `--remine-repo`; a run with different selection settings makes a new one.
- **SZZ blames are stored per (fix commit, file)** in
  `ci_cache/<owner>_<name>.szz.json.blames.jsonl` as they finish, and kept
  after the run. A resume blames only the pairs it doesn't have, and so does a
  later run with another window or PR selection on the same history (HEAD,
  blame settings and ignore-revs unchanged); `--remine-repo` clears it. Keep
  "Reuse clones" / `--skip-existing` on: a fresh clone with new commits
  changes HEAD, and the store starts over.

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

**Lead tools (F3, or the button under Join):** everything the lead needs, without a terminal. It shows the store and whether the admin token is saved (paste it once; it is checked with the store and written to `.env`). Set the form to the settings every PC should use and the number of PCs, then **Issue codes**: it plans the split, asks the store for one token per PC, and lists the codes with Copy selected, Copy all and a private file save. Only the settings that shape the data travel in the codes; machine settings such as workers and clone dir do not. The lower table shows every PC token with its split, expiry and last upload, and **Revoke selected PC** and **Revoke its whole split** cut access (press twice to confirm). Issuing again for the same split replaces its tokens.

With a join code, paste it into the **Join code** box and press Join (F2): the TUI checks it with the store, saves `.env`, fills the form with the lead's flags and your PC number, and shows `Team store: joined as …`. Those fields are then **locked**: the lead's settings cannot be changed on this PC (they are remembered in `.join.json`, so a restart keeps them locked). Settings about this machine (workers, clone dir, resume switches, the test fields) stay editable. **Leave** (or F-key via the button) unlocks the form and forgets the lead's settings; uploads then stop while your settings make a different split. Start still refuses, with the differences listed, if a locked setting somehow differs. The stats panel has a `Team store` line with the last upload, and quitting after Stop waits for the final upload (a second ctrl+q quits at once).

```bash
python tui.py
```

Every launch first runs the environment doctor (`doctor.py`). Before anything else it checks whether `main` has a newer
miner: it fetches `origin/main` and, by default, only **reports** it (`[update] main has a newer miner (X, you have Y)`,
with the command to run). Updating is opt-in: with `RIFFLE_AUTO_UPDATE=1`, when this checkout is on `main`, has no
uncommitted changes to tracked files and is simply behind, it fast-forwards and restarts once (`[update] miner 6.11.5 ->
6.12.0`) so every PC mines with the same version. Anything else (another branch, local edits, no network, not a git
checkout) is only reported or skipped, never forced. A plain `python build_dataset.py` from a terminal checks the same
way; a run started by the TUI, a run with no terminal (automation), `--test`, `--shard-plan` and `--audit` do not.
`RIFFLE_NO_AUTO_UPDATE=1` turns the check and the update off. It detects the
OS and distro (Ubuntu/Debian and derivatives, Fedora/RHEL, Arch, openSUSE,
Alpine, macOS, Windows, WSL) and its package manager (`apt`, `dnf`, `pacman`,
`zypper`, `apk`, `brew`, `winget`, `scoop`, `choco`). It then checks Python,
git, the Python packages, `GITHUB_TOKEN` (verified against GitHub, so the
`.env.example` placeholder doesn't count), the LLM setup, the optional
API-contract tools, disk and RAM, grouped by section, and offers fixes: it
pip-installs missing packages, asks for a token and saves it to `.env`
(gitignored, mode 600 where the OS allows), and offers the install command for
git, Claude Code and the API tools for your OS. Nothing is installed without a
yes. Run it on its own with `python doctor.py`, or `python doctor.py --check`
to only report (exit 1 on a failure). `RIFFLE_NO_DOCTOR=1` skips it,
`NO_COLOR=1` turns colour off. When nothing fails, the TUI opens after a 5-second
countdown (Enter opens now, Ctrl+C cancels, `RIFFLE_NO_WAIT=1` skips it). The miner
never starts by itself; you start a run from the TUI.

The API-contract tools are required while "API contract diff" is on (the
default). A missing one shows as a failure and gets an install offer; it does not
block launch, but that spec type's `api_contract_change` stays null. Turn the
option off (or `--no-api-contract`) to not need them. Each diffs one kind of spec: `oasdiff`
(OpenAPI), `buf` (protobuf), `graphql-inspector` (GraphQL; needs Node/npm).

Installs are fail-safe. If Node or Go is missing, the doctor shows the command that
installs it first, then the tool. `npm -g` goes to `~/.local` when the system prefix
needs root, and `~/go/bin`, `~/.local/bin` and the npm bin directory are added to
PATH for the run (the doctor says when to add them to your shell profile). A failed
install prints the manual link. A broken or missing venv module is retried with
`virtualenv`, and a doctor bug never blocks the TUI.

OS notes: on Windows, semgrep has no stable native build (its columns stay
null; use WSL for them) and the doctor offers `git config --global
core.longpaths true`. On WSL keep the repo in the Linux home directory, not
`/mnt/c`. On macOS without Homebrew, git comes from `xcode-select --install`.

On an externally managed Python (Ubuntu/Debian 23.04+, PEP 668) packages go
into `repo_miner/.venv`, created on first need; later launches re-run inside
it automatically. If `python3-venv` is missing, the doctor offers the `apt`
command.

The TUI log is selectable (drag, then ctrl+c); ctrl+y copies the whole log and
ctrl+b shows a QR code of the web view's link to scan with a phone (o opens
it here, c copies the link). The web view (read-only, LAN,
random key per launch) has log search, level filter, wrap / follow / pause /
expand, copy and download, and keyboard shortcuts (`?` lists them).

**Big picture** (web view, PC only): press `b`, click "Big picture", or open
the link with `#big`. A full-screen dark page readable from across a room:
the run state as a word (MINING, LLM PAUSED, STOPPED: ERROR, FINISHED,
OFFLINE), the percent done, the time, CPU and memory used, PRs mined, time
left, elapsed, PRs per minute, the repo being mined and repositories done,
any alert (an LLM stop, plan pause, low memory, health flag) and the last log
lines. Work runs down the left edge, time and system down the right. It keeps the screen awake while open;
`d` dims it, `b`, Esc or a click leaves it.

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

Quick runs: `--skip-big N` (TUI: "Skip big repos", "Big = more than N
commits", default 100000) skips repos whose `[~N commits]` tag in repos.txt is
larger (14 repos at 100k: llvm, rust, ClickHouse, swift, tensorflow, ...). In
the TUI, ctrl+n skips the repo selected in the table: a queued repo is marked
(press again to unmark), a running one asks for a second press and is stopped
with its finished PRs kept in the checkpoint. Skipped repos are never written
to `--out` and the shard split doesn't change, so a later run without the skip
mines them as usual.

Configure the batch on the left (repos file, output, PR cap, workers, LLM /
SZZ / CI toggles), press **ctrl+r** to start. The right side shows per-repo
progress, PR/min, ETA (shown once there are at least 3 points over at least 10
seconds, and it grows during a stall), LLM ok/fail + latency, LLM stop state, and the
log. **ctrl+x** stops (SIGINT); the checkpoint keeps finished PRs, so starting
again with "Reuse clones" on resumes.

## LLM backend and performance

- `--llm` defaults to **headless Claude Code** (`claude -p`, your Claude Code
  login, no API key; model `sonnet`, override with `LLM_MODEL`).
  `LLM_BACKEND=api` uses the OpenAI-compatible endpoint + `LLM_API_KEY`.
- **No row without its LLM answer (v3.0.0):** when a PR's LLM call fails, the
  miner retries that PR in place, up to 3 times with waits of 60, 120 and
  180 s (`LLM_RETRY_BACKOFF_S`), and later PRs queue behind it, so the whole
  miner pauses. The close-reason classifier is retried the same way instead
  of falling back to the rules. If a PR still has no answer, the repo stops:
  rows already done stay in the checkpoint, `--out` is not written, and the
  miner exits 3; `build_dataset.py` aborts the batch. Rerun to resume from
  that PR. A row in LLM scope is never written with empty LLM columns.
- **Plan limits (v3.0.0):** with headless Claude Code the miner reads the
  plan's usage (`claude -p /usage`, no model call) at most every 5 minutes.
  At 95% of a limit, or when a call is refused for one, LLM calls pause until
  the reset and then continue; PRs wait for their answers, so the miner
  pauses with them. A limit that
  resets more than 12 h away (a weekly cap) is not waited for: calls fail and
  the run stops. The total wait is capped by `llm_quota_max_wait_h`, and a limit message in
  non-JSON CLI output is recognised too. `--no-llm-quota-wait` turns this off.
- **Fewer, smaller calls (v3.0.0).** The flags are features, so only the rows
  the model trains on need them:
  - `--llm-scope training` (default): outside contributors (`CONTRIBUTOR`,
    `FIRST_TIME_CONTRIBUTOR`, `FIRST_TIMER`, `NONE`) who are not bots, plus a
    fixed 5% sample of the rest (`--llm-sample-pct`, the same PRs on every
    run). Other rows keep null LLM columns. `--llm-scope all` sends every row.
  - Docs-only, tests-only and lockfile/generated-only diffs are not sent;
    their flags would all be the neutral value.
  - The diff sent has 1 line of context. Lockfiles and generated, vendored,
    minified and snapshot files are cut to one line each, and the rest is
    capped at `--llm-max-diff-chars` (25000), dropping whole files and naming
    them.
  - The close-reason classifier is asked only when the rules find no reason
    (`--close-reason-llm other_only`, default); `always` asks for every
    unmerged PR with human activity.
- **Parallel PRs:** `--workers N` mines PRs in worker threads, consumed in PR
  order (output identical to a sequential run). The default is `min(6, CPUs)`,
  lowered to fit free RAM at about 0.8 GB per worker plus 1.5 GB (one run
  peaked at 5.5 GB with 6 workers); the miner says when it lowered it.
  `--llm-concurrency N` (default 4) caps simultaneous LLM calls.
- **Stop:** ctrl+c or the TUI's Stop ends the batch with one `[stop]` line,
  stops the running miner with its checkpoint kept, and exits 130. SZZ blame
  workers ignore the signal, so they no longer print tracebacks.
- The repo's `git log --numstat` is parsed once and reused per PR, hotspot
  complexity is cached per blob, and path classification is memoized.

## Limits, timeouts and data that leaves the machine

- **Git timeouts.** `RIFFLE_GIT_TIMEOUT_S` (300) for an ordinary git call, `RIFFLE_GIT_BLAME_TIMEOUT_S` (120) for a
  blame, `RIFFLE_GIT_LONG_TIMEOUT_S` (3600) for the whole-history log, and `RIFFLE_MAX_BLOB_BYTES` (4 MiB) for a file
  read at a commit. A call that times out is **unobserved** (`null`), never `0`. A clone that prints nothing for
  `--clone-stall-s` (600; `RIFFLE_CLONE_STALL_S`) is killed and retried.
- **Big PRs.** `--max-files-deep N` (default 300; 0 = off): beyond N files only the N with the largest churn get
  per-file features. The rest are `null`, and so are `LT`, `la_norm`, `ld_norm` and every `sum_*` column on that PR;
  `max_`, `min_` and `mean_` cover the measured files only.
- **Semgrep.** `RIFFLE_SEMGREP_RULES` (a file or directory) pins local rules; otherwise `--config auto` is used, which
  fetches rules from semgrep.dev, so that fetch leaves the machine. `RIFFLE_SEMGREP_PARALLEL` (default 2) limits
  concurrent semgrep runs. Metrics and the version check are always off.
- **LLM.** The LLM is on by default, and **the diff of each PR in scope is sent to the backend** (Anthropic through
  Claude Code, or the `api` endpoint), with obvious secrets redacted. Use `--no-llm` to send nothing.
- **Caches.** The historical CI map expires after 7 days (`CI_MAP_TTL_S`) and is rebuilt when it was built before the
  newest PR. An open PR's timeline is cached for 1 hour, a closed one for 14 days. `RIFFLE_NO_API_CACHE=1` turns the
  API cache off.
- **Reused rows.** Each line of the checkpoint carries a stamp of the settings that shape the data (miner major.minor,
  LLM on or off and model, CI history, SZZ, the observation cutoff day, and the stage and scope flags). On resume, a
  row with a different stamp is mined again and the log names the keys that differ. Rows from older versions with no
  stamp are accepted.

## Build a dataset (many repos)

`build_dataset.py` clones every repo listed in `repos.txt` (one GitHub URL per
line; `#` comments allowed) and mines them all into **one growing parquet**,
de-duplicated by `(repo, pr_number)`.

```bash
# quick smoke test — confirms the clone -> mine -> append loop works
python build_dataset.py --max-prs 15

# real dataset run — SZZ, CI history and the LLM flags are all on by default
python build_dataset.py --max-prs 1500

# without the LLM (no plan quota spent; the LLM columns stay empty)
python build_dataset.py --max-prs 1500 --no-llm
```

```text
flags (passed through to the miner):
  --repos-file F     list of GitHub URLs (default repos.txt)
  --out F            single dataset file, rows appended (default dataset.parquet)
  --clone-dir D      where repos are cloned (default cloned_repos)
  --max-prs N        cap PRs per repo
  --szz / --no-szz            SZZ fix-forward label (default on)
  --ci-history / --no-ci-history   historical CI-fail-rate features (default on)
  --llm / --no-llm            LLM semantic flags (default on)
  --snapshots / --no-snapshots     PR snapshots, assembled when the batch ends (default on)
  --skip-existing    reuse an already-cloned repo instead of re-cloning
```

Other `build_dataset.py` flags (the full table is in `ARCHITECTURE.md` section 16.2):

```text
  --keep-clones          keep each clone after mining (default: delete it, one clone on disk at a time)
  --repo-retries N       extra clone/mine attempts per repo (default 2, so 3 in all)
  --clone-stall-s S      kill a git clone that prints nothing for S seconds and retry it
                         (default 600, or RIFFLE_CLONE_STALL_S; 0 = never)
  --sync / --no-sync     send logs, health, tables and the manifest to the team store (default on,
                         but only when RIFFLE_SYNC_URL and RIFFLE_SYNC_TOKEN are set)
  --llm-model M          e.g. sonnet, opus, haiku
  --llm-backend B        claude_cli or api (default LLM_BACKEND, else claude_cli)
  --llm-diff-context N   context lines around each change in the diff sent to the LLM (default 1;
                         reaches the miner as LLM_DIFF_CONTEXT)
  --llm-quota-wait / --no-llm-quota-wait   wait for the Claude plan to reset (default on)
  --max-files-deep N     a PR touching more than N files gets per-file deep features (blame, line
                         history, scanners) for the N files with the most churn only (default 300;
                         0 = never cap; reaches the miner as RIFFLE_MAX_FILES_DEEP)
  --assemble-snapshots / --no-assemble-snapshots   build the snapshot tables when the batch ends (default on)
  --only-domain NAME     mine only repos tagged `[domain: NAME]` (tags are case-insensitive)
  --issue-tokens, --token-days D, --tokens-out FILE   with --shard-plan: one write token and join code
                         per PC; the tokens stop after D days; the codes are also saved to FILE
  --test, --test-prs N, --test-repo URL   smoke test: one repo (default pallets/flask), N PRs (10),
                         a fixed window, so every PC picks the same PRs
```

`--llm-diff-context`, `--llm-backend` and `--max-files-deep` are `build_dataset.py` flags that set an
environment variable for the miner, so a batch and its children agree. `mine_repo.py` reads the same
variables. A value that cannot be right (a negative number, `--window-from` without `--window-to`, a
malformed `--shard`, a bad `--remine-repo` name) is refused once, before anything is cloned. `--max-prs 0`
means no cap.

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
  outcomes. `label_ci_fail` counts only the **required/gating build** check,
  read from the merge commit's **push-triggered** workflow runs (plus other CI
  apps' checks): issue, schedule and comment workflows leave check runs on
  whatever commit was the default branch's head, and are not CI. A commit left
  with no such run has no verdict (`ci_observed` false), not a pass; it costs one
  extra API call per merged PR. Auxiliary checks (CodeQL, Scorecards, coverage)
  go to `label_ci_fail_optional` (diagnostic only). A hotfix is a later commit touching the same files within 7
  days whose subject says hotfix / hot-fix, rollback / rolled back, quickfix /
  quick fix, or urgent / emergency / critical followed by fix, patch, bug, issue,
  regression or security (`config.py`). Cherry-picks and backports are not
  hotfixes. A repo that never words its fixes that way (vite) has no positives.
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

## Merge-outcome labels

On by default since v2.0.0, so one run feeds both models: the breakage model
(`label_bad`, merged rows) and the merge model (`label_merged`, all rows), the
second target from `docs/adr/0009-merge-outcome-target.md`.
`--no-merge-outcome` (TUI: untick "Merge outcome (+ unmerged PRs)") gives the
v1 output: merged PRs and breakage labels only. Every shard PC must use the
same setting, or the merge model only sees some of the repos. Repos mined
before v2.0.0 need a re-mine to get merge labels.

- Rows: closed-unmerged PRs closed in the same span as the merged PRs
  mined, so the merged / unmerged mix is the repo's own. Their commits are
  fetched from `refs/pull/N/head` without local refs, so no `git log --all`
  scan sees them. Features are computed at the fork point as usual;
  post-merge labels (`label_bad`, `days_to_*`, ...) are null on these rows,
  and they never count as history for the repo-local columns. Repos mined in
  landed-commit mode, and `--pr-discovery git`, get no unmerged rows.
- `label_merged` on every row. `label_rework` on merged rows: a human
  non-author change request, or commits / force-pushes more than 5 minutes
  after open (`post_open_commits`, `post_open_force_pushes`,
  `changes_requested_reviews` hold the counts).
- On unmerged rows: `label_duplicate`, `label_unwanted`, `label_abandoned`,
  `label_ci_technical`, and `close_reason`. From the PR's timeline up to close,
  plus the required checks on its head commit. With `--llm`, a classifier
  reads the title, comments, reviews, labels, renames and closer and picks
  `close_reason` (one call per unmerged PR with human activity); without it,
  phrase and label rules decide (first of duplicate, unwanted, ci_technical,
  abandoned, else other). `close_reason_source` is `llm` or `rules`.
- PRs still open after 60 days (`abandon_days`) with no human response are
  rows too: unmerged, abandoned, `closed_at` null. Drafts and open PRs that
  got a response are left out.
- A repo is only mined in landed-commit mode when most closed PRs on the first
  page link to commits on the default branch. A low GitHub-merge share alone
  is also what a repo flooded with rejected AI PRs looks like.
- Also `created_at` and `closed_at`, for time splits.
- Every per-row stage, the LLM flags included, runs on unmerged rows too, so
  no feature is null only because a PR didn't merge (that pattern would give
  `label_merged` away). LLM spend grows with the row count.


## Training targets, flaky checks, maintainers (v5.0.0)

- **Targets (proposal Part 4)**, on every row so everyone trains on the same
  definitions: `outcome_3class` is `lands_clean` (merged, no rework),
  `lands_after_rework` or `goes_nowhere` (closed unmerged or abandoned);
  `nowhere_reason` is `abandoned`, `duplicate`, `unwanted`, `ci_technical` or
  `unknown` (read, no reason found), null when the timeline was unreadable.
- **Flaky checks** (`flaky.py`, `rules.mining.flaky_detection`): check runs
  are read with reruns (`filter=all`); each check's verdict is its latest
  run, and a check that failed then passed on rerun is a rerun-pass. After the
  repo is mined, a check with 3+ rerun-passes in the 90 days before a merge is
  flaky at that merge; a CI failure only from flaky checks is not counted in
  `label_ci_fail` / `label_bad`, and `ci_fail_flaky_only` says so.
  Point-in-time: only rerun-passes before the merge count.
- **Maintainers from history** (`maintainers.py`, proposal 2.2): every PR's
  timeline is read up front, and anyone who merged or closed someone else's
  PR is a maintainer from that moment. `author_confirmed_maintainer` records it
  for the PR author (point-in-time), and it decides "outside contributor" for
  `--llm-scope`, snapshot capture, the merge-signal outsider rates and the
  health check, because `author_association` shows today's status and reports
  private org members as `CONTRIBUTOR`. Snapshot actor roles use it too
  (saved as `snapshots_raw/<repo>/maintainers.json`). It catches people who
  triage; an employee who never merges or closes others' PRs still counts as
  outside.

## Merge-model signals (`features/merge_signals.py`)

The AIDev catalog's merge-model features the miner's own data can supply:

- Per PR: `has_linked_issue`, `body_headings`, `body_checklist_items`,
  `body_has_test_plan`, `agent_boilerplate` (from the body); `agent_name`
  (claude_code, codex, copilot, cursor, devin, jules, sweep: from login,
  head branch, git identity, watermark) and `head_branch_agent_prefix`;
  `author_account_age_days` (one cached `/users` call per author);
  `has_contributing`, `has_agents_md` (files at the base commit).
- Per repo, prior-only (PRs closed strictly before this one opened, last 365
  days; null under 5 PRs): `repo_outside_merge_rate` (+ count),
  `agent_recent_merge_rate` (+ count), `repo_median_first_response_hours`,
  `repo_agent_pr_load_30d`, `max_sim_open_prs` and `max_sim_recent_merged`
  (word-set Jaccard of title at open + body). The merge rates need
  unmerged rows, so they stay null with `--no-merge-outcome`.

The body is read at mining time (its edits are not in the timeline), so the
body features can see edits made after open.

## At-open snapshot (ADR 0010)

Diff features (sizes, path flags, blame, scanners, CVEs, LLM flags, ...) are
computed from the PR's head when it opened, not its final state, so commits
pushed under review don't leak into the features. The head is the newest
commit on the branch's first-parent line dated at or before open + 5 minutes;
a force-push after open is undone with GraphQL's `beforeCommit` (one batched
call per 25 PRs) and a fetch of that commit. Labels still use what landed.
`open_snapshot` is `exact`, `recovered` (from a force-push), `approx`
(force-push history unreadable) or `final` (not recoverable: diff features
use the final diff; drop or mask them when training).

## PR snapshots

A snapshot is a training row describing a PR at a later moment while it was
open, labelled with how the PR finally ended. The at-open row only shows PRs
as they opened; snapshots teach the model what "changes requested, author
pushed a fix" or "7 days without a maintainer reply" mean, so re-scoring a
live PR as events arrive gives a better answer. Two steps:

1. **Capture, while mining** (`snapshot_capture.py`, on by default,
   `--no-snapshots` turns it off). For rows in `--llm-scope` (outside
   contributors plus the 5% sample) the miner stores, under
   `snapshots_raw/<owner>_<name>/` (`--snapshot-dir`; `build_dataset.py
   --snapshot-root`): the PR object and timeline (trimmed), the force-pushes,
   every moment the PR's head changed while open, and for up to 5 of those
   heads (`--snapshot-max-heads`) the code features of fork point...head:
   diff sizes, path flags, deterministic, scanner, dependency and API-contract
   columns. Also each mined PR's merge-signal inputs (`pr_index.jsonl`). This
   is the part that can't wait: clones are deleted and force-pushed commits
   expire. The head at a moment uses ADR 0010's rule for the head at open.
   Git-history and blame features are not recomputed per head, and the LLM
   flags are the at-open ones, so snapshots add no LLM calls. Each repo
   logs what capture cost: `[snapshots] captured N PRs · H heads/PR · capture
   took S s, P% of PR mining time (~X% slower than without)`.
2. **Assembly** (`snapshots.py`), from the stored files and the mined rows,
   with no GitHub calls or clones. A batch with `--snapshots` runs it by itself
   when it ends (it needs every repo's finished at-open rows, so not per PR),
   writing `<out>_snapshots.parquet` and `<out>_commit_features.parquet` next to
   the dataset, e.g. `dataset_pc1of3_snapshots.parquet`. `--no-assemble-snapshots`
   skips it; a failure never fails the batch and prints the command to rebuild.
   The TUI lists both files, and the web view and big picture show
   "ASSEMBLING" and then the counts. To rebuild by hand, for example after
   changing the schedule (about 15 s for 1,200 PRs):

   ```bash
   python snapshots.py --dataset dataset.parquet --raw-root snapshots_raw \
       --out snapshots.parquet --commit-features-out commit_features.parquet
   ```

   - Moments: open (or ready-for-review for a draft, restarted at a reopen);
     first maintainer reply; each changes request (up to 3); first approval;
     the author's first push after a changes request; an outcome label or
     "marked as duplicate"; back to draft; 1, 3, 7, 14, 30 days in; 7 and 14
     days without human activity; 7 days without a maintainer reply after
     the author's last push or comment.
   - Clean-up: events within 30 min make one snapshot; unchanged states are
     dropped unless a clock; snapshots within 6 h with the same state keep
     the higher-priority one; nothing at or after close; at most 6 per PR.
     Bots count for nothing. `near_decided` marks the last hour before close
     and anything after a merge queue or auto-merge, for filtering in
     training; it is never a model input.
   - Each row: every mined column, with the head's code features, the merge
     signals as of the moment, and the state so far (time since start and
     since the last human activity, first maintainer reply, review rounds,
     changes requested, approvals, commits and force-pushes, whether the
     author pushed after the last review, comment counts, outcome label,
     draft, reopens, changes since the previous snapshot).
   - Training: `snapshot_weight` is 1 / the PR's snapshot count, so each PR
     adds up to 1 and slow PRs with many snapshots don't dominate (it is a
     weight, never a model input); `moment_bucket` (open, <1d, 1-3d, 3-7d,
     7-30d, >30d) is for reporting metrics per moment. Split by repo or PR.
   - Contracts: `contracts/mined_snapshot.schema.json` and
     `contracts/mined_commit_features.schema.json`.
   - `tests/test_snapshots.py` includes the leakage test: every snapshot is
     recomputed from data cut at its moment (later events, head changes,
     closes and first responses removed) and nothing may change.

## SZZ window

`--szz` blames only fix commits dated between the oldest mined merge and the
newest merge + `szz_maturity_days`: a fix outside that range can't count for
any mined PR, so the labels are identical (tested in
`tests/test_szz_equivalence.py`) for far less blame work (pallets/click, 60
PRs: 20 fix commits instead of 672). The cache records its window and is
reused only when it covers the one needed.

Within those, it blames only files whose history (renames followed, found
with a looser threshold so none are missed) the mined PRs' commits touched:
no other blame can land on a commit any label reads. Each mined PR's SZZ
entries stay exactly what a full build gives (checked on five repos, and
`test_relevant_pruning_follows_renames`); click went to 9 fix commits, and on
a 500-commit window SZZ ran 1.3-3.9x faster.

Also: one `git diff` per fix commit instead of one per file; HEAD's commits
come from the history cache the miner loads anyway, not a second full
`git log`; tasks go to the process pool a few at a time; and the cache and
checkpoint carry a fingerprint (SZZ version, HEAD, blame flags, ignore-revs
content), so results made under other settings are never reused. Indexes were
checked identical, order included, on itsdangerous, click, flask, werkzeug and
requests. Blame is ~90% of SZZ time, so these trim only a few percent.

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
merge_labels.py  merge-outcome labels from the PR timeline (ADR 0009)
aggregate.py     per-file dicts -> fixed max/mean/sum/min columns
features/
  diff_stats.py    Source 4a/4b + per-PR Kamei metrics
  history.py       Source 2 (as-of-base index, per-file, author, coupling)
  line_history.py  Source 3 (git-blame of modified lines)
  scanners.py      Source 4d (lizard, semgrep)
  deps_cve.py      Source 4e (OSV, GHSA)
  deterministic.py Source 5 (regex/parser checks)
  llm_flags.py     Source 6 (schema-constrained LLM, on by default)
mine_repo.py     orchestrator + CLI (one repo)
build_dataset.py batch driver: clone repos.txt + mine all into one dataset
snapshot_capture.py / snapshots.py   PR snapshots: capture while mining, assemble at batch end
naming.py        output names (PC tag, prefix/postfix), run stems
merge_shards.py  join the PCs' files after checking manifests and consistency.py
sync.py          send a PC's output to the team store (RIFFLE_SYNC_URL, ADR 0015)
join.py          join a split with the lead's code: writes .env and checks it
runlog.py        structured run trace (logs/run-*.trace.jsonl); trace_report.py summarises it
```

Also not listed above: `flaky.py`, `maintainers.py`, `scope.py`, `health.py`, `progress.py`,
`tui.py`, `dashboard.py`, `doctor.py`, `deps.py`, `scout.py`, `llm_compare.py`, `audit_export.py`,
`version.py` and `features/{merge_signals,repo_local,api_contract}.py`. The full module map is in
[`ARCHITECTURE.md`](ARCHITECTURE.md) section 3.2.
