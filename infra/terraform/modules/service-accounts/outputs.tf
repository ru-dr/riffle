output "emails" {
  description = "Google service account email per service."
  value       = { for k, sa in google_service_account.this : k => sa.email }
}

output "kubernetes_annotations" {
  description = "Annotation each Kubernetes service account needs (Helm values, phase 12): iam.gke.io/gcp-service-account = <email>."
  value       = { for k, sa in google_service_account.this : k => { "iam.gke.io/gcp-service-account" = sa.email } }
}

output "database_users" {
  description = "Cloud SQL IAM logins for the cloud-sql module's iam_users: riffle_app-bound services get no role here (granted after the first migration), the migration Job gets cloudsqlsuperuser."
  value = {
    for k, s in var.services : k => {
      email          = google_service_account.this[k].email
      database_roles = s.database_superuser ? ["cloudsqlsuperuser"] : []
    } if s.database || s.database_superuser
  }
}
