# Secret Manager entries (diagram: "Key vault · App key"). Pods never read these directly:
# External Secrets copies them into Kubernetes Secrets (phase 12).
#
# Two kinds:
# - set by hand (no value here): created empty, then filled with
#   gcloud secrets versions add <id> --data-file=...
# - set by Terraform: the value comes from var.values (e.g. Redis's AUTH string).

resource "google_secret_manager_secret" "this" {
  for_each = var.secrets

  project   = var.project_id
  secret_id = "${var.name}-${each.key}"

  # Stored only in this region: nothing leaves the installation's region.
  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }

  labels = merge(var.labels, { source = each.value.set_by })
  annotations = {
    purpose = each.value.purpose
  }
}

resource "google_secret_manager_secret_version" "this" {
  for_each = { for k, s in var.secrets : k => s if s.set_by == "terraform" }

  secret      = google_secret_manager_secret.this[each.key].id
  secret_data = var.values[each.key]
}
