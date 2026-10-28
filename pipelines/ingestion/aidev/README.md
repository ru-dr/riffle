# aidev

Pinned, verified access to [AIDev](https://huggingface.co/datasets/hao-li/AIDev),
the Hugging Face dataset of pull requests opened by AI coding agents. It records
exactly what is at one revision of the dataset, checks that it has not changed,
and downloads 11 of its 13 tables to a local folder, proving each file is
byte-for-byte the one the pin describes. It then uploads them to a Google Cloud
Storage raw bucket, verifies each object there, and deletes the local copy. The
large `pr_commit_details` table comes later.

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

### Uploading to the raw bucket

```bash
export AIDEV_RAW_BUCKET=<your raw bucket>          # or pass --bucket; never stored in the repo
pip install 'riffle-aidev-ingest[gcs]'             # the Google client, needed only for these two commands
python -m aidev_ingest upload --dry-run            # verify locally, look in the bucket, write nothing
python -m aidev_ingest upload                      # upload, verify in the bucket, delete the local copies
python -m aidev_ingest upload --tables repository  # a subset
python -m aidev_ingest verify-remote               # check the bucket against the manifest, no download
```

Uses Application Default Credentials (`gcloud auth application-default login`); no key
file is read. For each table, in this order:

1. Verify the local file again against the inventory. Bad bytes never go up.
2. Look in the bucket. An object already there is accepted only if it carries the expected
   SHA-256 (stored as object metadata); it is never replaced.
3. Create the object with a "must not already exist" precondition (`if_generation_match=0`),
   so Google refuses an overwrite, not just this code.
4. Compare the md5 the bucket reports with the local file's.
5. Only then delete the local file.

Objects land at `raw/aidev/<commit>/<table>.parquet`. Each run that changes anything also
writes a copy of the manifest to `raw/aidev/<commit>/_manifests/<timestamp>.json`, a new object
every time and never a replacement; the newest name is the current state. If a run dies
before writing its snapshot, the next run writes it, and `verify-remote` says so until then.

The bucket cannot take an object back: retention blocks deleting it, and the job account has no
delete role. That is why nothing is uploaded unverified and why `--dry-run` comes first.

The `sha256` stored on each object is metadata this tool wrote, so it is a statement of what
was uploaded, not independent proof. The independent checks are the object's size and the md5
that Google computes itself, compared with the local file's before anything is deleted.

Every step is safe to repeat. A run that dies, or loses its connection partway through a large
file, leaves no half-written object and has deleted nothing it had not verified; run the same
command again and it continues. Chunks are 8 MiB and retries last 15 minutes, so a brief
outage is absorbed; a longer one ends with a short message, not a traceback.

A table can be **held** (`hold_upload` in `pin.py`): `upload` skips it and says why, and
`--allow-held <table>` releases it deliberately. `pr_review_comments` is held because its
`diff_hunk` column holds source-code snippets from repositories with mixed licences.

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

- `pr_commit_details`: copied without `patch`, in pieces, because of its size.
- Deciding how to treat `diff_hunk` in `pr_review_comments`, which holds snippets
  of source code. It is held back from upload until then.
- Proving that the download service account itself cannot overwrite an object. The
  precondition and the tests cover this code; the account's IAM is checked when a job runs as it.
- Validation and the unified PR table.
