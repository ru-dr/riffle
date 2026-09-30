# ci-snapshot

Saves CI history for the dataset's 100 repositories before GitHub's new
retention rule deletes it.

**From 1 October 2026, GitHub keeps check runs, workflow runs and commit
statuses on public repositories for at most 90 days** (it was about 400).
Everything older is removed automatically and cannot be exported afterwards.
Code, commits, PRs and reviews are not affected; only "did CI pass" is.

This tool reads that CI history now and writes it to JSONL files on disk. It
only reads from GitHub; nothing is changed there.

## What it gathers

Four modes, each its own run. Window: **26 Aug 2025 → 2 Jul 2026**, the
commits and PRs older than 90 days, which are the ones deleted first. (The
last 90 days stay on GitHub for now; see "Pass 2" below.)

| Mode | What one record is | Fields | Why the model wants it |
| --- | --- | --- | --- |
| `commits` | A commit on the default branch | sha, date, PR number (from `(#N)`), **combined CI result** | The `ci_fail` label: did CI fail on the merge commit |
| `prs` | A PR closed in the window | number, merged or closed, created / closed / merged dates, author, base branch, last commit's sha and **combined CI result** | Pre-merge signal: was the PR merged while CI was red |
| `checks` | A sampled failed commit (50 per repository) | every check run on it (name, app, conclusion, timings; each Actions job is one) and every commit status (Prow and other third-party CI) | Which checks make a commit red: tells chronically failing or optional checks from real failures, to clean `ci_fail` across all commits |
| `runs` | A GitHub Actions run on a push to the default branch | workflow name and file, sha, **conclusion**, **attempt** (reruns), run number, created / started / updated times | Which workflow failed, reruns as a flakiness signal, durations |

The combined CI result is GitHub's `statusCheckRollup`: `SUCCESS`, `FAILURE`,
`ERROR`, `PENDING`, `EXPECTED`, or `null` when there was no CI. It covers
Actions and third-party CI (Prow, Buildkite, …). `runs` covers Actions only.

### What we are leaving behind

| Not captured | Why |
| --- | --- |
| Individual check results on every failed commit | One request per commit, over 150,000. `checks` takes a sample of 50 per repository instead |
| Which checks were required at the time | GitHub only exposes today's branch protection rules |
| Merge-queue runs (`merge_group`) | `runs` asks for push runs only |
| Earlier attempts of a rerun run | `runs` keeps the attempt count, not each attempt |
| Job and step detail inside Actions runs | One request per run; `checks` has job results for the sample |
| Scheduled and manual runs | Not tied to a merge |
| CI on every commit of every PR (each push, each failed attempt) | One query per commit on every PR branch: millions of requests |
| Actions runs triggered by pull requests (pre-merge runs) | Several times the volume of push runs. `prs` keeps the final CI state of each PR instead |
| Third-party check details (Prow, Buildkite runs) | Only their combined result, via `commits` and `prs` |
| Logs and artifacts | Gigabytes per run, and already on their own 90-day retention |
| Commits on other branches (release branches) | Labels are defined on the default branch; backports inherit their PR's label |

## Who runs what: exact commands

Each machine uses **its own GitHub account**: one token per real person, as
the dataset plan requires. Never share or rotate tokens. Each account has two
separate hourly budgets, **GraphQL 5,000 points** and **REST 5,000 requests**,
so the two windows on a machine do not slow each other.

Start both machines now. The retention change starts at 8:00 PM EDT
(midnight UTC).

### PC 1 (ru-dr)

```bash
cd ~/proyecto/riffle/pipelines/ingestion/ci-snapshot
git pull                              # latest version of the branch
go build -o ci-snapshot .
tmux new -s ci

# window 1 (GraphQL): combined CI result of every default-branch commit
./ci-snapshot -mode commits
# ...when it finishes (~7:10 PM), in the same window: half of the PRs
./ci-snapshot -mode prs -shard 1/2

# Ctrl+b then c  ->  window 2 (REST): per-check results for 50 failed
# PR merge commits per repository, from window 1's output as it finishes
./ci-snapshot -mode checks

# Ctrl+b then d to detach; `tmux attach -t ci` to come back
```

### PC 2 (friend)

Log in once as **your own** GitHub account: `gh auth login` (or set
`GITHUB_TOKEN` to your own token). Then, with the repository checked out on
branch `pipelines/ci-snapshot`:

```bash
cd riffle/pipelines/ingestion/ci-snapshot
go build -o ci-snapshot .
tmux new -s ci

# window 1 (GraphQL): the other half of the PRs, with their final CI result
./ci-snapshot -mode prs -shard 2/2
# ...when it finishes, in the same window: the most recent 90 days of commits
./ci-snapshot -mode commits -since 2026-07-02T00:00:00Z -until 2026-10-01T00:00:00Z -out ~/proyecto/riffle-data/ci-snapshot/pass2

# Ctrl+b then c  ->  window 2 (REST): Actions runs on default-branch pushes,
# all 100 repositories, smallest first
./ci-snapshot -mode runs
```

**After 8:00 PM, keep every window running.** GitHub's cleanup may not be
instant, and anything captured is kept.

No Go on the machine: use the prebuilt binary in [`bin/`](bin) for the
machine (`ci-snapshot-linux-amd64`, `ci-snapshot-windows-amd64.exe`,
`ci-snapshot-darwin-arm64` for Apple silicon, `ci-snapshot-darwin-amd64` for
Intel Macs), run from `bin/` so it finds `repos.txt`, e.g.
`cd bin && ./ci-snapshot-linux-amd64 -mode prs -shard 2/2`. Checksums are in
`bin/SHA256SUMS`. On macOS, if Gatekeeper blocks it:
`xattr -d com.apple.quarantine ci-snapshot-darwin-*`. On Windows, use two
terminal windows instead of tmux:
`ci-snapshot-windows-amd64.exe -mode prs` and
`ci-snapshot-windows-amd64.exe -mode runs`.

### Summary

| Machine | Window | Command | API | Output |
| --- | --- | --- | --- | --- |
| PC 1 | 1 | `./ci-snapshot -mode commits`, then `-mode prs -shard 1/2` | GraphQL | `pass1/`, then `prs/` |
| PC 1 | 2 | `./ci-snapshot -mode checks` | REST | `checks/` |
| PC 2 | 1 | `./ci-snapshot -mode prs -shard 2/2`, then `-mode commits` for the last 90 days | GraphQL | `prs/`, then `pass2/` |
| PC 2 | 2 | `./ci-snapshot -mode runs` | REST | `runs/` |

If quota is left over (tonight or tomorrow), a bigger per-check sample goes
in its own folder, since finished repositories are skipped:
`./ci-snapshot -mode checks -sample 200 -out ~/proyecto/riffle-data/ci-snapshot/checks-more`

Output lives under `~/proyecto/riffle-data/ci-snapshot/` (on PC 2 too, unless
`-out` is given).

## How long

Measured on 30 September 2026. Both machines in parallel, started around
6:00 PM EDT:

| Run | Machine | Volume | Expected time | Done by |
| --- | --- | --- | --- | --- |
| `commits` | PC 1 | ~500,000 commits, ~5,000 GraphQL points | 60–80 min | ~7:00–7:20 PM |
| `checks` | PC 1 | ~5,000 commits × 2 requests = ~10,000 REST | ~2 h (follows `commits`) | ~8:00–8:15 PM |
| `prs` | PC 2 half, then PC 1 half | ~300,000–450,000 PRs walked, ~4,000–9,000 points | ~1 h per half | ~7:00 PM (PC 2), ~8:00 PM (PC 1) |
| `runs` | PC 2 | tens of runs a day for most repositories; ~2,000 a day for pytorch | small and medium repositories ~1.5 h; giants after 8 PM | partial by 8 PM |

The progress screen shows a live ETA per run after a few minutes; trust that
over this table. `runs` does the smallest repositories first, so the most are
complete by the deadline; let it keep running afterwards. GitHub has not said
exactly when the cleanup runs, so it may not all vanish at 8:00 PM, and
anything captured is kept.

## Setup (each machine)

You need Go 1.22 or later and the GitHub CLI, logged in as yourself.

```bash
gh auth login                      # once, as your own GitHub account
git clone https://github.com/ru-dr/riffle.git
cd riffle && git checkout pipelines/ci-snapshot
cd pipelines/ingestion/ci-snapshot
go build -o ci-snapshot .
```

No Go on the machine? Build it on another one and copy the binary:
`GOOS=linux GOARCH=amd64 go build -o ci-snapshot .` (or `GOOS=windows` /
`GOOS=darwin`), plus `repos.txt`.

## Run

Use `tmux` so it keeps running if the terminal closes:

```bash
tmux new -s ci                     # window 1
./ci-snapshot -mode commits        # PC 1   (PC 2: -mode prs)

# Ctrl+b then c opens window 2
./ci-snapshot -mode runs -shard 1/2   # PC 1   (PC 2: -shard 2/2)

# Ctrl+b then d detaches; `tmux attach -t ci` comes back.
# Ctrl+b then 0 / 1 switches windows.
```

The screen shows overall progress, records captured, repositories done,
elapsed time and ETA, the quota left and when it resets (and **PAUSED** while
waiting for it), one line per repository being walked, the latest events,
and any repository that failed.

Output goes to `~/proyecto/riffle-data/ci-snapshot/<pass1|prs|runs>/`, one
`owner__name.jsonl` per repository, plus `run.log`. Change it with `-out`.

Other flags: `-workers 4` (repositories at once), `-plain` (log lines
instead of the screen), `-since` / `-until` (the window), `-repos`.

## Stopping, resuming, errors

- **Stop any time** with `q`, `Ctrl+C`, or by closing the terminal. Every page
  is written to disk as it arrives, with a cursor per repository. Run the
  same command again and it continues; finished repositories (`.done`) are
  skipped.
- **Quota running low** (under 100 GraphQL points or 50 REST requests): it
  pauses until GitHub's reset time, then carries on.
- **Secondary limits, 403 or 429:** it waits for `Retry-After` (or the reset
  time) and retries.
- **Big histories timing out (502/504):** it retries the page at 50, 25, then
  10 records.
- **Network errors and other 5xx:** up to 6 retries with growing waits.
- **A repository that still fails** is shown in red and in `run.log`; the rest
  continue. Rerun the same command to retry it.

Your other GitHub use during the run: `git push` / `pull` and the website are
unaffected. `gh` commands that use GraphQL (like `gh pr list`) may be refused
while the GraphQL quota is spent.

## After it finishes

1. Check each mode's `run.log` for `FAILED` lines, and rerun if there are any.
2. Bring PC 2's `prs/` and `runs/` folders to PC 1 (or both to one shared
   place). The file names never collide: shards write different repositories.
3. Archive it and keep a copy off both machines. Tonight's data cannot be
   fetched again.

   ```bash
   cd ~/proyecto/riffle-data
   tar czf ci-snapshot-2026-09-30.tar.gz ci-snapshot
   sha256sum ci-snapshot-2026-09-30.tar.gz > ci-snapshot-2026-09-30.tar.gz.sha256
   ```

   Then put it in a private repo's release, or a cloud drive.

## Pass 2: the most recent 90 days

Not urgent: these stay on GitHub for now and expire day by day. Any day this
week, on either machine:

```bash
./ci-snapshot -mode commits -since 2026-07-02T00:00:00Z -until 2026-10-01T00:00:00Z -out ~/proyecto/riffle-data/ci-snapshot/pass2
./ci-snapshot -mode prs     -since 2026-07-02T00:00:00Z -until 2026-10-01T00:00:00Z -out ~/proyecto/riffle-data/ci-snapshot/prs-pass2
./ci-snapshot -mode runs    -since 2026-07-02T00:00:00Z -until 2026-10-01T00:00:00Z -out ~/proyecto/riffle-data/ci-snapshot/runs-pass2
```

After that, CI keeps expiring after 90 days, so a small daily archiver is
needed from now on (it is in the dataset plan).
