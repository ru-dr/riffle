output "repository" {
  description = "Repository resource name, for permissions."
  value       = google_artifact_registry_repository.this.name
}

output "url" {
  description = "Image prefix: <url>/<service>:<tag>, e.g. us-east4-docker.pkg.dev/<project>/<repo>/intake:1.0.0."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.this.repository_id}"
}
