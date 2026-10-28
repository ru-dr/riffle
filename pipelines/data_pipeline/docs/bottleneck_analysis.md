# Pipeline bottleneck analysis

How long each stage takes in Airflow, what held the pipeline back, and what was changed. All
numbers come from real runs of the two DAGs in `dags/riffle_data_pipeline.py` on a laptop with
Docker Desktop (8.2 GB memory limit, `LocalExecutor`), on the pinned AIDev revision
(`pull_request` 361,296 rows and `repository` 25,402 rows).

## How to see the Gantt chart

Airflow UI (http://localhost:8080) > the DAG > pick a run > **Gantt**. Compare these two runs:

| DAG | Run | What it shows |
| --- | --- | --- |
| `riffle_data_pipeline_sequential` | `manual__2026-10-27T05:37:05` | the baseline: one task after another |
| `riffle_data_pipeline` | `manual__2026-10-27T05:38:07` | `validate` and `preprocess` side by side |

## Per-task timings (after the fixes below)

Seconds from the start of the run. One run of each, so expect a few seconds of variation.

| Task | Sequential: start, end, took | Parallel: start, end, took |
| --- | --- | --- |
| `acquire` | 0.0, 13.9, 13.9 | 0.0, 6.9, 6.9 |
| `validate` | 14.7, 31.9, 17.2 | 7.5, 30.0, 22.5 |
| `preprocess` | 33.2, 44.8, 11.5 | 7.5, 21.2, 13.7 |
| `features` | 45.8, 50.0, 4.2 | 30.6, 34.7, 4.1 |
| `alert_on_anomalies` | 33.3, 33.7, 0.4 | 30.6, 31.0, 0.4 |
| **Whole run** | **52.6** | **35.0** (33 % shorter) |

The critical path of the parallel DAG is `acquire` > `validate` > `features`. `preprocess` finishes
about 9 seconds before `validate` and then waits, so it is not the bottleneck.

## What we found, in the order we found it

### 1. The first parallel run was killed by the kernel

`validate` exited with code -9 (SIGKILL, the out-of-memory killer). Measured peak memory per
stage, alone, inside the container:

| Stage | Peak memory | Time |
| --- | --- | --- |
| `validate` | 5,627 MB | 33 s |
| `preprocess` | 3,256 MB | 21 s |
| `features` | 2,453 MB | 13 s |

Run together, `validate` and `preprocess` need about 8.9 GB, more than the 8.2 GB limit. Memory,
not CPU, was what limited parallelism. Most of it was text nobody needed: the stages loaded every
pull request body to ask only whether it was null, or how long it was.

### 2. A failed task did not fail the run

With `validate` killed, the DAG run still finished as **success**. Airflow judges a run by its last
tasks, and both of ours (`features` and `alert_on_anomalies`) had succeeded. Fix: `features` now
also depends on `validate`, so it is skipped when validation fails and the run is marked failed. It
also means features are never built from data that did not pass its checks. Tested by forcing a
failure (an impossible row-count minimum): `validate` failed, `features` became `upstream_failed`,
the run was **failed**, and the failure alert fired with the right task and reason.

### 3. Adding the model made `slices` the longest stage

A sixth task, `slices` (the baseline model and the per-group metrics, `docs/bias_analysis.md`),
runs after `features`. In a full run of the parallel DAG in the container it took 38.5 s, against
24.6 s for `validate`, and the whole run finished in about 88 s. It is now the longest stage and
sits on the critical path (`acquire` > `validate` > `features` > `slices`). The earlier tables
above were measured before it existed, so they do not include it.

## Optimisations applied

| Change | Effect |
| --- | --- |
| `validate` reads the free-text columns cut to their first character, batch by batch (`data_io.py`) | peak memory 5,627 > 1,680 MB; 33 > 15 s alone |
| `preprocess` computes title and body lengths and does not carry the text forward | `pull_requests_clean.parquet` 351 > 15 MB |
| `features` reads those lengths instead of the text | peak memory 2,453 > 453 MB; 13 > 3 s alone |
| `validate` and `preprocess` run in parallel (they only read the raw data) | whole run 52.6 > 35.0 s |
| `validate` and `preprocess` depend on the two Parquet files, not the raw folder | a re-download no longer reruns them: the folder also holds `manifest.json` with a download timestamp, so its hash changed every time |

The outputs did not change: the same 332,404 feature rows, the same label rate (0.7424) and the
same quarantined rows.

## What is still slow, and what we did not do

- **`validate` is the critical path** (15 s alone, 22 s beside `preprocess`). Most of it is Great
  Expectations computing about 100 metrics per table. Validating a sample would be faster but would
  miss rare bad rows, so we did not do it.
- **`acquire` takes 7 to 14 s when there is nothing to download.** It re-checks the SHA-256 of the
  362 MB file on every run. That is deliberate (a run proves its input is intact), but it could be
  skipped when size and modification time are unchanged.
- **Parallel is slower per task than alone** (`validate` 22.5 s beside `preprocess`, 15 s alone):
  both compete for the same disk and CPU.
- **One run each.** The two DAGs were run once after the fixes. Variation between runs (for
  example `acquire` at 6.9 s and 13.9 s) is about as large as some of the differences above, so
  treat the 33 % as an estimate, not a benchmark.
