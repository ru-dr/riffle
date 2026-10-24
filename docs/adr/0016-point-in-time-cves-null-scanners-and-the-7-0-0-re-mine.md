# 0016: Point-in-time CVE data, null-when-unobserved scanners and the 7.0.0 re-mine

- **Date:** 2026-10-23
- **Status:** Proposed
- **Owners:** #5 Data ingestion

## Context

An audit of the repo miner (`pipelines/ingestion/repo_miner/`) found columns whose values did not mean what the contract says. The CVE columns were looked up when the miner ran, so a vulnerability disclosed after a PR opened could appear on that PR (leakage), and a withdrawn record still counted. A semgrep scan that did not run (tool missing, bad exit code, bad JSON, timeout) gave `0`, and a failed base scan counted every head finding as new. A git call that timed out, a check-run page that was cut off and a manifest the parser could not read could each turn into a number instead of `null`. Several labels also matched the PR's own merge commit or commits (`label_revert`, `label_hotfix`), and `label_szz_bug` was `false` when SZZ never ran. These fixes change what existing columns mean, so a dataset mined with 6.x is not comparable with one mined with 7.0.0.

## Decision

- **CVE columns are as of the PR's open time.** `deps_cve.compute` takes `as_of` (the PR's open time; for a snapshot, the time the head was first seen). OSV and GHSA records published after `as_of`, or withdrawn, are dropped. A record that cannot be dated, or an `as_of` that cannot be read, makes all CVE columns `null`.
- **`vuln_has_fix_available` is `False` or `null`, never `True`.** It is `False` when no introduced record has a fix and `null` when any has one, because OSV does not date the release of a fix.
- **Unobserved is `null`, never `0`.**
  - A semgrep scan that failed or did not run gives `null` for all three semgrep columns. Findings are compared as a multiset keyed on rule, path and normalised matched code, with no line number.
  - A lizard parse error gives `null` for the three CCN columns.
  - `n_deps_added`, `n_deps_upgraded`, `n_deps_downgraded` and `n_major_bumps` are `null` when any touched manifest is unparsed or unreadable.
  - A git call that times out (`RIFFLE_GIT_TIMEOUT_S`, `RIFFLE_GIT_BLAME_TIMEOUT_S`, `RIFFLE_GIT_LONG_TIMEOUT_S`, `RIFFLE_MAX_BLOB_BYTES`) is `null`.
  - A failed or truncated check-run, status or workflow-run page gives a `null` `check_conclusion`.
  - `api_contract_change` is `null` when any changed spec is unobserved, unless another spec is already `breaking`.
  - `label_szz_bug` and `days_to_szz_fix` are `null` when SZZ did not run. A new optional `szz_window_closed` says whether a `false` is mature.
- **Labels do not match the PR's own commits.** A revert needs `#N` in its subject or `Reverts owner/repo#N` in its body, and is searched from the default-branch tip.
- **7.0.0 is a MAJOR version and needs a re-mine** before retraining. The checkpoint stamp (`_ckpt`) makes a resumed run mine again any row written with different data-shaping settings and names the keys that differ. Unstamped rows from older versions are accepted. Snapshots can be re-assembled from `snapshots_raw/` without mining; the dismissed-review fix needs the PR captured again.
- **Contracts:** `mined_dataset` 1.3, `mined_snapshot` 1.4 and `mined_commit_features` 1.1 (see `docs/contracts.md` and `contracts/REFERENCE.md`). New optional columns `llm_diff_truncated` and `szz_window_closed`. New keywords `x-riffle-non-input` and `x-riffle-diff-derived`, which `pipelines/training/frames.py` reads to decide what the model may see and what to mask on `open_snapshot == final` rows.
- **Not changed (deferred):** `author_prs_merged_repo` and `similar_pr_count` keep `0` for none, which the contract defines. Body-derived features stay as read at mining time because the timeline has no edit history. An unmerged PR whose head cannot be fetched is dropped with a warning. A wiped team store is not detected by the sync state.

## Options not taken

| Option | Why not |
| --- | --- |
| Keep CVE data as of mining time | A disclosure after the PR opened would reach the model as if it were known at open |
| Keep `0` for a scan or parse that did not run | `0` means measured and found none; the model would learn a failure as a clean result |
| Report `vuln_has_fix_available` as `True` from today's data | OSV does not date a fix's release, so a fix may not have existed at open |
| Fix in place and keep mixing 6.x and 7.0.0 rows | The labels and many features changed meaning; `merge_shards.py` already refuses mixed miner versions |
| Give unmerged PRs with an unfetchable head a row with null features | Left for later, see Open |

## Consequences

- Every dataset mined before 7.0.0 must be mined again before it is used to retrain. Null shares can go up for CVE, semgrep, dependency and CI columns, because failures that were `0` or a guess are now `null`.
- `pipelines/training/frames.py` masks the full list of 125 diff-derived columns on `final` rows, derives non-input columns from the contract, and groups its split by repo, PR and shared head commit, so splits for repos with mixed-case names change.
- Contracts, `ARCHITECTURE.md` (sections 5, 6 and 21) and the miner README change in the same PR as the code, as the contract rules require.
- The miner's defaults send data out of the installation: the LLM flags send each in-scope diff to the backend, and semgrep `--config auto` fetches rules from semgrep.dev. This is not changed here. `RIFFLE_SEMGREP_RULES` pins local rules and `--no-llm` sends no diff. The miner builds training data and is not on the scoring path, so the invariants on the LLM and on scoring are not affected, but the "nothing leaves the installation by default" invariant is documented as not holding for the miner.
- Point-in-time scoring is stronger: CVE columns no longer carry knowledge from after the PR opened.

## Revisit if

- The store gains a listing API, so a wiped store can be detected in the sync state.
- A timeline source with edit history is available, so body features can be as of open.
- The share of CVE or semgrep columns that are `null` after the re-mine is too high to train on.
- OSV starts dating the release of a fix.

## Open

- Whether to give unmerged PRs with an unfetchable head a null-feature row instead of dropping them.
- Whether the miner's LLM and semgrep defaults should change to keep data inside the installation.
- The options above come from the audit notes; the owners have not reviewed them.
