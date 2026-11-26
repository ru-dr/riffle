output "ids" {
  description = "Secret id per key, e.g. { redis-auth = \"riffle-staging-redis-auth\" }: what External Secrets reads."
  value       = { for k, s in google_secret_manager_secret.this : k => s.secret_id }
}

output "set_by_hand" {
  description = "Secrets that still need a value added with gcloud."
  value       = sort([for k, s in var.secrets : google_secret_manager_secret.this[k].secret_id if s.set_by == "hand"])
}

output "resource_ids" {
  description = "Full resource id per key, for permissions (phase 9)."
  value       = { for k, s in google_secret_manager_secret.this : k => s.id }
}
