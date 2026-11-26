# Phase 6: Cloud Storage buckets (diagram: Object storage, Score log, and the storage behind
# Loki and Tempo). Who may read or write each bucket is set per service in phase 9.

module "storage" {
  source = "../../modules/storage-buckets"

  project_id    = var.project_id
  location      = var.region
  name_prefix   = "${var.project_id}-${var.environment}"
  force_destroy = var.storage_force_destroy
  labels        = local.labels

  buckets = {
    # ADR 0024. Rows are never rewritten, and how long they are kept is still open in the ADR,
    # so nothing is deleted; after 90 days they move to cheaper storage.
    "score-log" = {
      purpose             = "Score log: every score with the features the scorer saw, Parquet per tenant and day"
      nearline_after_days = 90
    }
    # MLflow artifacts. Versioning so an overwritten model file can be recovered.
    "models" = {
      purpose                  = "Model registry artifacts: global models and repo models"
      versioning               = true
      noncurrent_versions_kept = 5
    }
    # Training snapshots and the history backfill of new tenants (ADR 0001, ADR 0025).
    "snapshots" = {
      purpose             = "Training snapshots and backfill output"
      nearline_after_days = 30
    }
    # Loki and Tempo delete old data themselves; the bucket rule is a backstop one day later.
    "loki" = {
      purpose           = "Loki log chunks"
      delete_after_days = var.log_retention_days + 1
    }
    "tempo" = {
      purpose           = "Tempo traces"
      delete_after_days = var.trace_retention_days + 1
    }
  }

  depends_on = [module.project_services]
}
