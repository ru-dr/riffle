# Pipeline bottleneck analysis

How long each stage takes in Airflow, what held the pipeline back, and what was changed. All
numbers come from real runs of the two DAGs in `dags/riffle_data_pipeline.py`, on a laptop with
Docker Desktop (8.2 GB memory limit, `LocalExecutor`), on the pinned AIDev revision
(`pull_request` 361,296 rows and `repository` 25,402 rows). The timings are the task start and end
times Airflow records, which is the data its Gantt chart draws. No screenshots are included.

To look at the Gantt chart yourself: Airflow UI (http://localhost:8080) > the DAG > pick a run >
the Gantt view.

## The short version

- **Memory was the real constraint, and fixing it was the optimisation that worked.** Peak memory of
  `validate` fell from 5.6 GB to 1.7 GB and the cleaned table from 351 MB to 15 MB, with identical
  outputs.
- **Running `validate` and `preprocess` in parallel gave no reliable speed-up.** One pair of runs
  favoured it and a later pair went the other way (table below). We kept both DAGs; the parallel one is
  not proven faster.
- **`slices` is now the longest task**, about 36 to 39 s and more than half of a run.
- **A failed task used to leave the run marked as a success.** That is fixed.

## Runs we measured

Seconds. "Tasks" is how many tasks the DAG had at the time: `slices` was added later, so the first pair
has five.

| Run | Tasks | `acquire` | `validate` | `preprocess` | `features` | `slices` | Whole run |
| --- | --- | --- | --- | --- | --- | --- | --- |
| sequential (earlier) | 5 | 13.9 | 17.2 | 11.5 | 4.2 | | **52.6** |
| parallel (earlier) | 5 | 6.9 | 22.5 | 13.7 | 4.1 | | **35.0** |
| sequential (later) | 6 | 5.8 | 10.9 | 8.6 | 3.3 | 36.1 | **67.2** |
| parallel (later, first run after a restart) | 6 | 24.5 | 29.2 | 18.0 | 4.9 | 39.4 | **101.0** |
| parallel (later, second run) | 6 | 15.9 | 28.0 | 17.6 | 5.5 | 38.4 | **91.4** |

In the "later" sequential run, `alert_on_anomalies` also took 7.4 s instead of the usual under 1 s,
because an email setting pointed at a mail server that was switched off. That setting is removed and it
does not touch the other tasks.

## How to read those numbers

The two things being compared are the **phase where `validate` and `preprocess` both run**. Run one
after the other they take the sum of their times; run together, the longer of the two.

| Pair | Sequential (sum) | Parallel (longer one) | Result |
| --- | --- | --- | --- |
| earlier | 17.2 + 11.5 = 28.7 s | 22.5 s | parallel about 6 s better |
| later | 10.9 + 8.6 = 19.5 s | 28.0 s | parallel about 8 s worse |

The sign flips. When the two tasks run side by side, each is slower than it is alone (`validate` took
28.0 s beside `preprocess` against 10.9 s in the sequential run), which fits two heavy tasks competing for
the same CPU and disk inside Docker. That explanation is an inference from the slower task times: we did not
profile it, and we did not record how many CPUs Docker had.

Run-to-run noise is also large, and it is as big as the effect. `acquire`, which does the same work every
time (re-checking the SHA-256 of a 362 MB file), ranged from 5.8 s to 24.5 s across these runs. The runs
were not controlled, and the laptop was under memory pressure at times. The "earlier" pair's totals differ by
17.6 s, and 7.0 s of that is `acquire` alone (13.9 s against 6.9 s), a task that is not part of the comparison.
So the 33 % shorter total that an earlier version of this document reported should not be read as the
effect of running in parallel.

**What we can say:** on this machine, parallelising these two tasks is not a dependable win. It may help on a
machine with spare CPU and disk, or if the tasks run on separate workers, but we have not shown that.

## What we found, in the order we found it

### 1. The first parallel run was killed by the kernel

`validate` exited with code -9 (SIGKILL, the out-of-memory killer). Measured peak memory per
stage, alone, inside the container:

| Stage | Peak memory | Time |
| --- | --- | --- |
| `validate` | 5,627 MB | 33 s |
| `preprocess` | 3,256 MB | 21 s |
| `features` | 2,453 MB | 13 s |

Run together, `validate` and `preprocess` need about 8.9 GB, more than the 8.2 GB limit. Most of it was
text nobody needed: the stages loaded every pull request body to ask only whether it was null, or how
long it was.

### 2. A failed task did not fail the run

With `validate` killed, the DAG run still finished as **success**. Airflow judges a run by its last
tasks, and both of ours (`features` and `alert_on_anomalies`) had succeeded. Fix: `features` now
also depends on `validate`, so it is skipped when validation fails and the run is marked failed. It
also means features are never built from data that did not pass its checks. Tested by forcing a
failure (an impossible row-count minimum): `validate` failed, `features` became `upstream_failed`,
the run was **failed**, and the failure alert fired with the right task and reason.

### 3. Adding the model made `slices` the longest stage

A sixth task, `slices` (the baseline model and the per-group metrics, `docs/bias_analysis.md`),
runs after `features`. It took 36.1 to 39.4 s in the runs above, against 10.9 to 29.2 s for `validate`. It
sits on the critical path (`acquire` > `validate` > `features` > `slices`) in both DAGs, so shortening
`validate` or `preprocess` can no longer shorten a run by much.

## Optimisations that worked

These were measured with each stage run alone, inside the container, which avoids the contention above.

| Change | Effect |
| --- | --- |
| `validate` reads the free-text columns cut to their first character, batch by batch (`data_io.py`) | peak memory 5,627 > 1,680 MB; 33 > 15 s |
| `preprocess` computes title and body lengths and does not carry the text forward | `pull_requests_clean.parquet` 351 > 15 MB |
| `features` reads those lengths instead of the text | peak memory 2,453 > 453 MB; 13 > 3 s |
| `validate` and `preprocess` depend on the two Parquet files, not the raw folder | a re-download no longer reruns them: the folder also holds `manifest.json` with a download timestamp, so its hash changed every time |

The outputs did not change: the same 332,404 feature rows, the same label rate (0.7424) and the
same quarantined rows.

## Tried, with no reliable gain

- **Running `validate` and `preprocess` in parallel** (the `riffle_data_pipeline` DAG). See above. It does
  not need more memory than the machine has any more, but it is not dependably faster.

## What is still slow, and what we did not do

- **`slices` (36 to 39 s)** is the biggest target. It fits three models and a threshold optimiser; we did
  not try to speed it up.
- **`validate` is the next largest** (15 s alone). Most of it is Great Expectations computing about 100
  metrics per table. Validating a sample would be faster but would miss rare bad rows, so we did not do it.
- **`acquire` takes 6 to 25 s when there is nothing to download.** It re-checks the SHA-256 of the
  362 MB file on every run. That is deliberate (a run proves its input is intact), but it could be
  skipped when size and modification time are unchanged. It is also the noisiest task.
- **Few, uncontrolled runs.** Each configuration was run once or twice. A proper comparison would repeat
  each several times on a quiet machine and report the spread.
