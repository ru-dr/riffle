output "names" {
  description = "Bucket name per key, e.g. { score-log = \"riffle-infra-platform-staging-score-log\" }."
  value       = { for k, b in google_storage_bucket.this : k => b.name }
}

output "urls" {
  description = "gs:// URL per key."
  value       = { for k, b in google_storage_bucket.this : k => b.url }
}
