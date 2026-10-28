# Riffle data pipeline

A reproducible pipeline that turns the public [AIDev](https://huggingface.co/datasets/hao-li/AIDev)
dataset (pull requests opened by AI coding agents) into a validated, labelled feature table, and
checks whether a baseline model treats every group of pull requests fairly.

```
acquire --> validate -----+--> features --> slices
   |                      |
   +----> preprocess -----+

validate --> alert_on_anomalies          (Airflow only)
```

| Stage | What it does | Main output |
| --- | --- | --- |
| `acquire` | downloads two tables at a pinned dataset commit and checks every file's SHA-256, size, rows and columns | `data/raw/<commit>/` |
| `validate` | schema and statistics with Great Expectations; anomaly detection | `reports/schema_stats.json`, `validation.json`, `anomalies.json` |
| `preprocess` | cleans, joins, sets bad rows aside with a reason | `data/processed/pull_requests_clean.parquet`, `quarantine.parquet` |
| `features` | closed pull requests only, the `merged` label, features known at creation, a time-based split | `data/processed/features.parquet` |
| `slices` | baseline model scored per group, two bias mitigations | `reports/bias_report.md`, `slices_metrics.json` |

This folder is the assignment's `Data-Pipeline/` folder. It lives in the Riffle monorepo, so the path is
`pipelines/data_pipeline/`.

| Assignment asks for | Here |
| --- | --- |
| `dags/` | `dags/riffle_data_pipeline.py` |
| `data/` | `data/` (contents are not in git; DVC tracks them) |
| `scripts/` | `scripts/` |
| `tests/` | `tests/` |
| `logs/` | `logs/` (contents are not in git) |
| `dvc.yaml` | `dvc.yaml`, `dvc.lock`, `params.yaml`, `data/sample/features.parquet.dvc` |
| `README.md` | this file |

## Set up

Needs Python 3.11 to 3.13 and git. No account, token or GCP access: the dataset is public. All commands
run from this folder.

```bash
git clone <repository-url>
cd riffle/pipelines/data_pipeline

python -m venv .venv
source .venv/bin/activate            # Windows (PowerShell): .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

`requirements.txt` installs the AIDev download code from `../ingestion/aidev`, so install from this
folder. The first install takes several minutes (about nine in our clean-clone test) because of DVC, Great
Expectations and scikit-learn. With conda instead:
`conda env create -f environment.yml && conda activate riffle-data-pipeline`; `environment.yml` just
installs `requirements.txt`, and has not been run by hand.

## Run it

```bash
python -m pytest tests -q            # the tests, offline, about a minute
dvc repro                            # the whole pipeline: downloads about 364 MB, then runs every stage
```

`dvc repro` runs only what changed: a second run says every stage is up to date. To run one stage, or
all of them without DVC, run the script: `python -m scripts.validate`. Each script has `--help`.

Try it in seconds without the download: a 20,000-row sample of the features table is tracked by DVC.

```bash
dvc pull data/sample/features.parquet.dvc        # needs access to the DVC remote (see below)
python -m scripts.slices --processed data/sample --reports reports_sample
```

Without remote access, make the sample from a full run instead: `python -m scripts.make_sample`. It is
deterministic, so you get the same file. Until you do one or the other, `dvc status` on a fresh clone lists the
sample as "not in cache"; that is expected.

### Settings

`params.yaml` holds everything you may want to change. DVC reruns a stage when a parameter it lists changes.

| Parameter | Default | Meaning |
| --- | --- | --- |
| `aidev.commit` | `b37f9fd1...` | the dataset revision, a commit hash (a tag could move) |
| `aidev.tables` | `pull_request,repository` | which tables to download |
| `validation.iqr_factor` | 3.0 | how far past the quartiles a value must be to count as an outlier |
| `validation.min_rows` | 300000 / 20000 | the fewest rows each table may have |
| `features.test_fraction` | 0.2 | the newest share of pull requests held out as the test set |
| `slices.min_group_size` | 500 | groups with fewer test rows are listed but left out of the gaps |
| `slices.validation_fraction` | 0.15 | the newest share of training used to choose thresholds |
| `slices.top_languages` | 10 | languages beyond these become `other` |
| `slices.mitigate_on` | `agent` | the attribute the bias fixes target |

## Code structure

```
dags/riffle_data_pipeline.py   two Airflow DAGs from one definition: parallel, and a sequential baseline
scripts/
  acquire.py       stage 1: reads params.yaml, downloads and verifies (uses ../ingestion/aidev)
  validate.py      stage 2: Great Expectations suites, statistics, reports
  anomalies.py     the anomaly checks, one small function each
  preprocess.py    stage 3: clean, join, quarantine
  features.py      stage 4: label, features, time split
  slices.py        stage 5: model, per-group metrics, mitigations, report
  schema.py        the two tables' columns, types, null limits and formats, in one place
  data_io.py       loading tables; the memory-light loader for text columns
  alerts.py        Slack and email alerts from environment variables
  logging_setup.py one logging setup for every stage
  make_sample.py   the DVC-tracked sample
tests/             pytest, offline; test_dag_structure.py needs Airflow and skips without it
docs/              bias_analysis.md and bottleneck_analysis.md
reports/           written by the stages; bias_report.md and slices_metrics.json are tracked by git
```

Every script has the same shape: a pure function that does the work and can be tested, a `run()` that reads
and writes files, and a `main()` that sets up logging, catches errors, and returns an exit code (0 or 1).

## Data versioning with DVC

- **`dvc.yaml`** defines the stages, their inputs, parameters and outputs. **`dvc.lock`** records the hash of every
  input and output of the last run. Both are in git, so a commit says exactly which data it produced.
- **`data/sample/features.parquet.dvc`** tracks the sample dataset. DVC keeps the file's content in a cache and in the
  remote; git keeps only this small pointer.
- **Data stays out of git:** `data/raw`, `data/processed`, the DVC cache and the logs are gitignored.
- **Go back to an older dataset:** `git checkout <commit> -- dvc.lock params.yaml`, then `dvc checkout` (or `dvc pull`).
  `dvc metrics diff` compares the bias metrics between commits.

### Same bytes on every machine

DVC hashes file bytes, so two things are pinned to keep hashes equal across operating systems: `.gitattributes`
checks every text file out with LF line endings (a Windows checkout would otherwise differ from Linux), and the
scripts write their reports with LF. One hash is deliberately not stable: `dvc.lock` records the whole
`data/raw/<commit>/` folder, which includes `manifest.json` with the download time, so that one hash changes on each
download. The Parquet files inside do not change, and the stages that read them depend on the files, not the folder,
so nothing downstream reruns.

**Checked on a fresh clone** (a new virtual environment, no cache, no remote, none of the local files): `pip install`
and all tests passed, and `dvc repro` ran every stage. The features table, the cleaned table, the quarantine file and
every report, including `bias_report.md` and `slices_metrics.json`, came out with hashes identical to the committed
`dvc.lock`, and a second `dvc repro` skipped every stage.

### Two ways to get the data

1. **Anyone, no remote:** `dvc repro` downloads from Hugging Face and verifies every file against the pinned
   checksums. This is the supported path for a fresh clone.
2. **With a remote:** the project uses a Google Cloud Storage remote named `gcs`. Its URL is not in the repository,
   so the bucket name stays private. To use your own bucket:

   ```bash
   dvc remote modify --local gcs url gs://<your-bucket>/dvc
   gcloud auth application-default login
   dvc pull            # restores every tracked output without recomputing
   dvc push            # uploads new outputs
   ```

   `--local` writes to `.dvc/config.local`, which git ignores.

## Airflow

The DAGs run in the Docker stack in `../../infra/docker/airflow` (Airflow 3.3, `LocalExecutor`, needs 8 GB of memory
given to Docker). Its README covers starting it; this pipeline adds a virtualenv for the stage packages, mounts of
this folder, and the alert settings. After `docker compose up -d --build` in that folder:

```bash
docker compose exec airflow-scheduler airflow dags list              # riffle_data_pipeline and ..._sequential
docker compose exec airflow-scheduler airflow dags unpause riffle_data_pipeline
docker compose exec airflow-scheduler airflow dags trigger riffle_data_pipeline
```

Open http://localhost:8080 for the UI and the **Gantt** tab. Tasks run the same `python -m scripts.<stage>` commands as DVC.
Failed tasks alert; only `acquire` is retried, because only it uses the network. `features` waits for `validate`, so
data that failed validation is never turned into features, and a failed validation fails the run.

The DAG structure tests need Airflow, so they run in the container:
`docker compose exec airflow-scheduler python -m pytest /opt/riffle/data_pipeline/tests/test_dag_structure.py --noconftest`
(install pytest there first with `python -m pip install pytest`).

## Logging and alerts

- **Logs:** each stage logs to the terminal and to `logs/<stage>.log` (rotating, UTC timestamps). Set `RIFFLE_LOG_DIR` to
  write elsewhere. Pointing it at the repository's top-level `logs/` folder sends them to the local Loki and Grafana stack
  in `../../infra`. Airflow keeps its own task logs.
- **Alerts** go to Slack and/or email when a task fails or `validate` finds anything. They are configured only by
  environment variables, never in files in git; with none set, the alert is written to the log.

  | Variable | Use |
  | --- | --- |
  | `RIFFLE_SLACK_WEBHOOK_URL` | a Slack incoming-webhook URL (must be https) |
  | `RIFFLE_SMTP_HOST`, `RIFFLE_SMTP_PORT`, `RIFFLE_SMTP_USER`, `RIFFLE_SMTP_PASSWORD` | the mail server (STARTTLS) |
  | `RIFFLE_ALERT_FROM`, `RIFFLE_ALERT_TO` | sender, and comma-separated recipients |

  For Airflow, put them in `infra/docker/airflow/.env` (gitignored); Compose passes them through.

## Data quality

`validate` runs Great Expectations (24 checks on `pull_request`, 14 on `repository`) and the anomaly checks in
`scripts/anomalies.py`:

| Kind | Example | Severity |
| --- | --- | --- |
| schema | a column is missing, extra, or the wrong type | error |
| missing values | more nulls than `schema.py` allows (a null `closed_at` is fine for an open pull request) | error |
| invalid format | a timestamp or URL that does not match its pattern | error |
| outliers | a value beyond `iqr_factor` x IQR (measured on a log scale for counts) | warning |
| contradictions | closed before created; `state` disagrees with `closed_at` | warning if rare (under 0.1 % of rows), error if common |
| references | a pull request whose repository is not in the table | error |

An **error** fails the stage; a **warning** is reported and alerted but does not stop the run. `preprocess` sets
contradictory, duplicate and orphaned rows aside in `quarantine.parquet`, each with its reason, so nothing is dropped silently.

## Bias analysis

The data has no personal attributes, so the analysis slices by agent, language, repository size and licence. The model's
quality differs sharply by agent (true-positive rate 85 % for Claude Code, 20 % for Copilot), because merge rates drifted
between the training and test periods. Per-agent thresholds close the gap to about 4 points at a cost of 7.7 points of overall
accuracy. The full process, numbers, trade-offs and limits are in [`docs/bias_analysis.md`](docs/bias_analysis.md).

## Performance

[`docs/bottleneck_analysis.md`](docs/bottleneck_analysis.md) has the per-task Airflow timings, what was slow, and what changed:
validate and preprocess run in parallel, text columns are no longer loaded where only their length matters (peak memory of
`validate` fell from 5.6 GB to 1.7 GB), and the whole run took about a third less time.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| a task exits with code -9 in Airflow | out of memory: give Docker 8 GB or more |
| `ModuleNotFoundError: aidev_ingest` | install from this folder: `python -m pip install -r requirements.txt` |
| `dvc: command not found` | activate the virtual environment |
| `... is inside a git repository and is not ignored` | the downloader refuses to write raw data where git could commit it; keep `data/raw` in `.gitignore` |
| `dvc pull` fails with a remote or credentials error | you have no access to the project's bucket; use `dvc repro`, or set your own remote as above |
| download is slow or rate limited | set `HF_TOKEN` to a free Hugging Face token |

## Known limits

- The slice and bias results come from one time split and one run, with no confidence intervals.
- Slack and email alerts are tested with fakes; delivery to a real webhook or mail server has not been tried.
- Tested on Windows with Docker Desktop. Linux and macOS paths follow the same commands but have not been run by hand;
  CI runs the tests and `dvc dag` on Linux.
