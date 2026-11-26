# One Google service account per service, and the Workload Identity link that lets that
# service's Kubernetes service account act as it (diagram: Cluster platform, pod identity).
# No keys are ever created: a pod gets short-lived tokens through Workload Identity.
# Every permission is in permissions.tf.

resource "google_service_account" "this" {
  for_each = var.services

  project      = var.project_id
  account_id   = "${var.name_prefix}-${each.key}"
  display_name = "${each.key} (${var.name_prefix})"
  description  = each.value.description
}

# Only the Kubernetes service account <namespace>/<ksa> in this cluster may act as the Google
# account. A pod in another namespace, or with another service account, gets nothing.
resource "google_service_account_iam_member" "workload_identity" {
  for_each = var.services

  service_account_id = google_service_account.this[each.key].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.workload_pool}[${each.value.namespace}/${coalesce(each.value.ksa, each.key)}]"
}
