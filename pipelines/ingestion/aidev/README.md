# aidev

Pinned, read-only access to [AIDev](https://huggingface.co/datasets/hao-li/AIDev),
the Hugging Face dataset of pull requests opened by AI coding agents. Today this
package does one thing: it records exactly what is at one revision of the dataset
and checks that it has not changed. Downloading the tables into the raw layer is
the next step and is not built yet.

## The pin

`aidev_ingest/pin.py` fixes the revision as a **commit hash**
(`b37f9fd1613cfec55886105bb76721918d015dc7`, tag `v5`, cutoff 2026-03-31), not a
tag or branch. Tags and `main` can move; a hash cannot. It also lists every table
with how it will be fetched (`full`, `pop_filtered` or `skip`) and why.

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

## Tests

```bash
python -m pytest pipelines/ingestion/aidev/tests -q                  # offline, what CI runs
RUN_NETWORK_TESTS=1 python -m pytest pipelines/ingestion/aidev/tests -q   # also talks to the Hub
```

The offline tests use a fake hub and a real Parquet file written to a temporary
folder. One of them checks that the committed inventory matches the pin and the
fetch plan.

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

Download into the raw layer (pop filter, dropping `patch`, checksum
verification against the inventory), validation, and the unified PR table. The
fetch plan in `pin.py` is the intended scope.
