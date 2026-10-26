# aidev

Pinned, verified access to [AIDev](https://huggingface.co/datasets/hao-li/AIDev),
the Hugging Face dataset of pull requests opened by AI coding agents. It records
exactly what is at one revision of the dataset, checks that it has not changed,
and downloads 11 of its 13 tables to a local folder, proving each file is
byte-for-byte the one the pin describes. Uploading to Google Cloud Storage, and
the large `pr_commit_details` table, come later.

## The pin

`aidev_ingest/pin.py` fixes the revision as a **commit hash**
(`b37f9fd1613cfec55886105bb76721918d015dc7`, tag `v5`, cutoff 2026-03-31), not a
tag or branch. Tags and `main` can move; a hash cannot. It also lists every table
with how it is fetched and why:

| Policy | Tables | Meaning |
| --- | --- | --- |
| `as_is` | 11 (about 3.2 GB) | downloaded byte for byte and checked by SHA-256 |
| `pruned` | `pr_commit_details` (12.4 GB) | later: written without the `patch` column |
| `skip` | `all_pull_request` (6.7 GB) | every repository's PRs; we use the AIDev-pop subset |

`scope` says what a table covers, from the dataset card: `all_repository`,
`all_user` and `all_pull_request` cover every repository; the rest cover only
AIDev-pop (repositories with more than 100 stars).

`expected/aidev-v5.inventory.json` is the committed inventory of that revision:
for every file its size, Git LFS sha256 (the Hub's own checksum, so nothing is
downloaded to know it), and for Parquet the row count, row groups and column
types. Later stages compare what they download against it.

## Commands

Run from `pipelines/ingestion/aidev`. Needs Python 3.11+, `huggingface_hub` and
`pyarrow`. No Hugging Face login: the dataset is public and no token is sent.

```bash
python -m aidev_ingest inventory --out expected/aidev-v5.inventory.json
python -m aidev_ingest inventory --revision v4     # look at another tag, hash or branch
python -m aidev_ingest check                       # does the Hub still match the pin?
```

`inventory` reads file listings and Parquet footers through range requests, so
it takes about 15 seconds and a few megabytes however large the tables are.
`check` exits 1 if any file, size, checksum, row count or column differs from the
committed inventory, or if a planned table or a column to drop is missing.

### Downloading

```bash
python -m aidev_ingest download --dry-run          # what would be downloaded; writes nothing
python -m aidev_ingest download                    # the 11 as_is tables into data/raw/aidev/<commit>/
python -m aidev_ingest download --tables repository,related_issue
python -m aidev_ingest verify                      # re-check the folder offline
python -m aidev_ingest closure                     # do the pop tables only point at real PRs?
```

How a table is downloaded, smallest first:

1. Check free disk space.
2. Download into `.partial/` with `hf_hub_download`: anonymous, pinned to the
   commit hash, resumable.
3. Verify against the inventory: size, SHA-256 (the Hub's own LFS checksum), row
   count, column names and types.
4. Move into place only if all of that passes. A table is complete and verified,
   or absent.

Rerunning skips verified tables. A file that already exists but fails
verification stops the run and is never overwritten: raw data is not replaced
silently, and a mismatch is something you should look at.

The output folder defaults to `data/raw/aidev` at the repository root, which
`.gitignore` covers. The command refuses a folder inside a git repository that is
not ignored, so downloaded data cannot be picked up by `git add`. Pass `--out` to
use another location.

`manifest.json` is written next to the data after each table, with the commit, a
digest of the inventory it was checked against, and each table's size, checksum,
row count, columns and state (`local_verified`). It holds relative paths only.
`verify` re-checks the folder against it with no network.

`closure` reports, for the AIDev-pop tables that have a `pr_id`, how many rows
point at a PR that is not in `pull_request`. It reports on the data and never
stops a download; the dataset card says these tables are pop-only, and this
checks that. Run on its own, it first verifies the folder and refuses to read
files that fail.

## Tests

```bash
python -m pytest pipelines/ingestion/aidev/tests -q                  # offline, what CI runs
RUN_NETWORK_TESTS=1 python -m pytest pipelines/ingestion/aidev/tests -q   # also talks to the Hub
```

The offline tests use a fake hub and downloader and real Parquet files written to a
temporary folder. They cover skipping verified files, refusing to overwrite a bad
one, discarding a corrupt download, an interrupted download, the disk check, the
dry run, the manifest, `verify` catching a flipped byte, and the closure report.
One checks that the committed inventory matches the pin and the fetch plan. The
network tests download the two smallest tables for real.

## Things learned the hard way

- **`dataset_info(...).sha` is not the commit you asked for.** For this dataset it
  returned one hash for tag `v3` where `list_repo_refs` gives another, and a third
  when handed a full commit id. `resolve()` uses `list_repo_refs` for names and
  treats a full hash as already resolved. A network test guards this.
- **The file listing does honour the revision**, which is what the pin relies on:
  the same call at the `v3`, `v4` and pinned hashes returns 18, 11 and 13 Parquet files.
- **A stale cached token breaks anonymous reads.** `hub.py` sets
  `HF_HUB_DISABLE_IMPLICIT_TOKEN` before importing the library and passes
  `token=False`.
- Works on `huggingface_hub` 0.36 and 2.3.

## Not here yet

- Uploading the verified files to Google Cloud Storage, then deleting the local
  copies once the upload is verified.
- `pr_commit_details`: copied without `patch`, in pieces, because of its size.
- Deciding how to treat `diff_hunk` in `pr_review_comments`, which holds snippets
  of source code, before that table is uploaded anywhere.
- Validation and the unified PR table.
