# ci-snapshot

Saves the combined CI result of every default-branch commit in the dataset's
repositories before GitHub's 90-day retention for check runs, workflow runs and
commit statuses on public repositories (from 1 October 2026) deletes the older
ones.

One JSON line per commit, one file per repository:

```json
{"repo":"pallets/flask","branch":"main","oid":"8285adf…","committed_at":"2026-06-10T18:03:21Z",
 "pr":null,"headline":"update dev dependencies","ci_state":"FAILURE","captured_at":"2026-09-30T21:32:58Z"}
```

`ci_state` is GitHub's `statusCheckRollup` for the commit: `SUCCESS`,
`FAILURE`, `ERROR`, `PENDING`, `EXPECTED`, or `null` when it had no CI.

## Run

```bash
cd pipelines/ingestion/ci-snapshot
go build -o ci-snapshot .
tmux new -s ci                      # survives closing the terminal
./ci-snapshot                       # pass 1: 2025-08-26 → 2026-07-02, into ~/proyecto/riffle-data/ci-snapshot/pass1
```

Pass 2, the most recent 90 days (not urgent):

```bash
./ci-snapshot -since 2026-07-02T00:00:00Z -until 2026-10-01T00:00:00Z -out ~/proyecto/riffle-data/ci-snapshot/pass2
```

The token comes from `GITHUB_TOKEN`, or `gh auth token`. `-plain` prints log
lines instead of the progress screen; `-workers` sets how many repositories
run at once (default 4).

## Stopping and resuming

Press `q`, or kill it: every page is on disk as it arrives, with a cursor per
repository. Run the same command again and it continues; finished
repositories (`.done`) are skipped. Progress is also in `run.log` in the
output directory.

## Limits

- GraphQL: 5,000 points an hour; a page of up to 100 commits costs 1. When
  fewer than 100 points remain, it pauses until GitHub's reset time.
- Secondary limits and 429/403: honours `Retry-After`, else backs off.
- 502/504 or a GraphQL timeout on a large history: retries the page at 50,
  25, then 10 commits.
- Network errors and other 5xx: up to 6 retries with backoff; a repository
  that still fails is reported and the rest carry on. Rerun to retry it.
