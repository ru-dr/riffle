# scorelog-writer (Python)

Turns the `score-log` topic into the score log (ADR 0024): one row per score, with the inputs the
model saw, as Parquet files on object storage. Each tenant's files are encrypted with that tenant's
own data key (ADR 0025, ADR 0028). Logging is never on the scoring path: the scorer publishes and
moves on, and this writer catches up.

```
score-log ─► scorelog-writer ─┬─ tenant's data key ◄── tenant_keys (Postgres), unwrapped by KMS tenant-kek
                              └─► gs://<score-log bucket>/score_log/tenant=<id>/date=<YYYY-MM-DD>/<HHMMSS>-<random>.parquet.enc
```

Invariants:
- **Nothing is lost.** A message is acknowledged only after its file is stored. A failed write is
  retried; after 5 deliveries it goes to the dead letter and is kept for 7 days.
- **Rows are never rewritten.** Files are only created (`if_generation_match=0`, and the account has
  `objectCreator`, which cannot overwrite). A message delivered twice can appear in two files, so
  readers keep one row per `/delivery_id`.
- **A file opens only with its tenant's key and under its own name.** Deleting the tenant deletes
  the key, and every copy becomes unreadable.
- **The plain key never leaves memory.** Postgres holds it wrapped by KMS.

## What happens to a row

| Case | Result |
| --- | --- |
| A valid row | Held until its tenant-and-day buffer has `BATCH_ROWS` rows or has waited `FLUSH_SECONDS`, then written in one file and acknowledged |
| The same `delivery_id` twice in one file | Written once (`duplicate`) |
| Breaks `contracts/score_log.schema.json` | Acknowledged, logged, counted (`invalid`) |
| Tenant being deleted, or gone | Acknowledged, not written (`tenant_gone`): its data is being deleted |
| Tenant has no key yet (active or suspended) | A key is made, wrapped by KMS and stored, then used |
| Postgres, KMS or the bucket fails | Nacked and retried (`riffle_score_log_write_failures_total`) |
| SIGTERM (rollout, scale-down) | Stops pulling, writes every held batch, exits |

## The file

- **Format:** `RFLE1` (5 bytes), then a 12-byte nonce, then the AES-256-GCM ciphertext of a
  Parquet file (zstd). The object's name is the associated data.
- **Columns:** the message in flat form (ADR 0012), one column per JSON Pointer: `/delivery_id`,
  `/rank_score`, `/features/LA`, and so on. `contracts/flatten.py`'s `unflatten` rebuilds the
  message. A value that is not a scalar (an empty `features` on an unranked row) is stored as JSON
  text.

To decrypt one file, with Postgres and `tenant-kek` decrypt access (for example from the writer's
pod):

```bash
kubectl -n riffle-workers exec deploy/scorelog-writer -- \
  python -m scorelog_writer read gs://riffle-infra-platform-staging-score-log/score_log/tenant=48213/date=2026-11-27/120000-0a1b2c3d4e5f.parquet.enc
```

## Configuration

In the cluster every value comes from `riffle-platform-settings` (made by Terraform), the chart, or
the Application (`infra/k8s/apps/staging/22-scorelog-writer.yaml`).

| Variable | Source in the cluster | Default |
| --- | --- | --- |
| `PUBSUB_PROJECT_ID`, `SUBSCRIPTION_SCORE_LOG_WRITER` | settings | required |
| `BUCKET_SCORE_LOG` | settings | required |
| `TENANT_KEK_ID` | settings (the KMS key `tenant-kek`) | required |
| `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_NAME` | the chart (`cloudSql.enabled`) and settings | `127.0.0.1`, `5432`, required, required |
| `BATCH_ROWS`, `FLUSH_SECONDS` | Application | `500`, `60` |
| `KEY_CACHE_SECONDS` | Application | `600` |
| `MAX_HELD_MESSAGES` | — | `5000` |
| `HEALTH_PORT`, `METRICS_PORT` | — | `8080`, `9090` |

It runs as the `scorelog-writer` service account (Workload Identity). That account may:
- read the `score-log-writer` subscription;
- create objects in the `score-log` bucket;
- wrap and unwrap with `tenant-kek`;
- log in to Cloud SQL as a `riffle_app` member.

Ports: `8080` serves `GET /healthz` and `GET /readyz`. `9090` serves `GET /metrics`:
- `riffle_score_log_rows_total{result}`
- `riffle_score_log_files_total`
- `riffle_score_log_write_failures_total`
- `riffle_score_log_buffered_rows`
- `riffle_score_log_flush_seconds`

The alert "score log files not written" (`infra/terraform/modules/monitoring`) and the dashboard
row "Score log writer" (`infra/k8s/platform/dashboards`) read them.

## Build, test, deploy

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest tests -q     # the Postgres tests skip without WRITER_TEST_DATABASE_URL (see tests/test_keys_postgres.py)
```

| Step | Where |
| --- | --- |
| Tests | CI job `scorelog-writer`, with a Postgres service |
| Image | `.github/workflows/images.yml` builds `services/scorelog-writer/Dockerfile` from the repo root |
| Deploy | `infra/k8s/apps/staging/22-scorelog-writer.yaml`. Put the new tag in `image.tag` |

## Not done yet

- **No reader yet.** ADR 0024's training loader for the log (beside `pipelines/training/frames.py`)
  is not built.
- **Rotating `tenant-kek`.** New KMS key versions are used automatically, but re-wrapping existing
  data keys is not built.
- **Retention.** How long rows are kept is still open in ADR 0024. The bucket moves files to
  Nearline after 90 days and deletes nothing.
