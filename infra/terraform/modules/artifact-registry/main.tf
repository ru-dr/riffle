# The container registry (diagram: "Container registry"): CI pushes every service's image
# here, and the cluster's nodes pull from it.

resource "google_artifact_registry_repository" "this" {
  project       = var.project_id
  location      = var.region
  repository_id = var.name
  format        = "DOCKER"
  description   = "Riffle service images"

  # A tag, once pushed, always points at the same image: what was tested is what runs.
  docker_config {
    immutable_tags = true
  }

  # Keep the last few images of each service; drop untagged leftovers after a while.
  cleanup_policy_dry_run = false

  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = var.keep_recent_images
    }
  }

  cleanup_policies {
    id     = "delete-old-untagged"
    action = "DELETE"
    condition {
      tag_state  = "UNTAGGED"
      older_than = "${var.untagged_max_age_days * 86400}s"
    }
  }

  labels = var.labels
}
