output "raw_bucket" {
  description = "Raw-layer bucket."
  value       = google_storage_bucket.raw.name
}

output "artifact_registry" {
  description = "Image path prefix for docker push."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.riffle.repository_id}"
}

output "service_accounts" {
  description = "Job identities."
  value = {
    ingest_download = google_service_account.ingest_download.email
    ingest_validate = google_service_account.ingest_validate.email
  }
}

output "datasets" {
  description = "BigQuery datasets."
  value = {
    raw_aidev = google_bigquery_dataset.raw_aidev.dataset_id
    unified   = google_bigquery_dataset.unified.dataset_id
  }
}
