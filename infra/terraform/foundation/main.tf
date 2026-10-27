# Milestone 1 of the ingestion plan: storage, warehouse, image registry,
# identities and a budget alert. Cloud Run, Workflows and Scheduler come later.

locals {
  # Only the APIs this configuration uses. Storage and BigQuery are already on in
  # the project; listing them keeps this file the record of what is required.
  apis = [
    "storage.googleapis.com",
    "bigquery.googleapis.com",
    "artifactregistry.googleapis.com",
    "iam.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "serviceusage.googleapis.com",
    "billingbudgets.googleapis.com",
  ]
}

resource "google_project_service" "api" {
  for_each = toset(local.apis)

  project = var.project_id
  service = each.value

  # Turning the API off would break resources that other things still use.
  disable_on_destroy = false
}

# ---------------------------------------------------------------------------
# Raw layer. Append-only in practice: versioned, no public access, and a
# retention period so no version can be permanently destroyed early. An
# authorised user can still replace or delete the *live* object (the old version
# stays recoverable), so the real guards are the job accounts having no delete
# role and the writer using if_generation_match=0. Objects are laid out as
# raw/<source>/<revision>/<table>.parquet.
# ---------------------------------------------------------------------------
resource "google_storage_bucket" "raw" {
  name     = var.raw_bucket_name
  location = upper(var.region)

  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  versioning {
    enabled = true
  }

  retention_policy {
    retention_period = var.raw_retention_days * 24 * 60 * 60
    is_locked        = false
  }

  # Scratch space for partial downloads. Anything under scratch/ is disposable.
  lifecycle_rule {
    condition {
      age            = 7
      matches_prefix = ["scratch/"]
    }
    action {
      type = "Delete"
    }
  }

  # Old versions of an object only exist if something replaced it; clear them out
  # after the retention period plus a margin.
  lifecycle_rule {
    condition {
      days_since_noncurrent_time = var.raw_retention_days + 60
    }
    action {
      type = "Delete"
    }
  }

  depends_on = [google_project_service.api]
}

# ---------------------------------------------------------------------------
# Warehouse. raw_aidev holds external tables over the raw bucket; unified holds
# the cleaned PR table built from them.
# ---------------------------------------------------------------------------
resource "google_bigquery_dataset" "raw_aidev" {
  dataset_id  = "raw_aidev"
  location    = var.region
  description = "External tables over gs://${var.raw_bucket_name}/raw/aidev. Read-only views of the raw layer."

  delete_contents_on_destroy = false

  depends_on = [google_project_service.api]
}

resource "google_bigquery_dataset" "unified" {
  dataset_id  = "unified"
  location    = var.region
  description = "Layer 2: one clean row per PR, merged across sources."

  delete_contents_on_destroy = false

  depends_on = [google_project_service.api]
}

# ---------------------------------------------------------------------------
# Image registry for the ingestion jobs.
# ---------------------------------------------------------------------------
resource "google_artifact_registry_repository" "riffle" {
  repository_id = "riffle"
  location      = var.region
  format        = "DOCKER"
  description   = "Container images for Riffle pipelines."

  depends_on = [google_project_service.api]
}

# ---------------------------------------------------------------------------
# Identities. One service account per job, each with only what it needs and no
# key files. The workflow account is added with the workflow in milestone 6.
# ---------------------------------------------------------------------------
resource "google_service_account" "ingest_download" {
  account_id   = "ingest-download"
  display_name = "Ingestion: download to raw"

  depends_on = [google_project_service.api]
}

resource "google_service_account" "ingest_validate" {
  account_id   = "ingest-validate"
  display_name = "Ingestion: validate raw and build unified"

  depends_on = [google_project_service.api]
}

# Download: create objects and read them back (to skip what already exists).
# No delete permission, matching the append-only rule. Whether this role can
# still overwrite an existing object is untested; the writer must use
# if_generation_match=0 either way.
resource "google_storage_bucket_iam_member" "download_create" {
  bucket = google_storage_bucket.raw.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_service_account.ingest_download.email}"
}

resource "google_storage_bucket_iam_member" "download_read" {
  bucket = google_storage_bucket.raw.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.ingest_download.email}"
}

# Validate: read raw, write to the two datasets, and run query jobs.
resource "google_storage_bucket_iam_member" "validate_read" {
  bucket = google_storage_bucket.raw.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.ingest_validate.email}"
}

resource "google_bigquery_dataset_iam_member" "validate_raw_editor" {
  dataset_id = google_bigquery_dataset.raw_aidev.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.ingest_validate.email}"
}

resource "google_bigquery_dataset_iam_member" "validate_unified_editor" {
  dataset_id = google_bigquery_dataset.unified.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.ingest_validate.email}"
}

resource "google_project_iam_member" "validate_job_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.ingest_validate.email}"
}

# ---------------------------------------------------------------------------
# Budget. Alerts only; it does not stop spending. Emails go to the billing
# account's administrators.
# ---------------------------------------------------------------------------
data "google_project" "this" {
  project_id = var.project_id
}

resource "google_billing_budget" "monthly" {
  billing_account = var.billing_account_id
  display_name    = "riffle monthly"

  budget_filter {
    # The Billing API stores the project number, not the id. Using the id here
    # shows a permanent diff on every plan.
    projects = ["projects/${data.google_project.this.number}"]
  }

  amount {
    specified_amount {
      currency_code = "USD"
      units         = tostring(var.monthly_budget_usd)
    }
  }

  threshold_rules {
    threshold_percent = 0.5
  }
  threshold_rules {
    threshold_percent = 0.9
  }
  threshold_rules {
    threshold_percent = 1.0
  }

  depends_on = [google_project_service.api]
}
