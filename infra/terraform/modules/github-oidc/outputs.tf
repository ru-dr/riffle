output "workload_identity_provider" {
  description = "Goes in google-github-actions/auth as workload_identity_provider."
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "ci_images_email" {
  description = "Goes in google-github-actions/auth as service_account, in jobs that push images."
  value       = google_service_account.ci_images.email
}

output "ci_terraform_email" {
  description = "Goes in google-github-actions/auth as service_account, in the Terraform job on main."
  value       = google_service_account.ci_terraform.email
}
