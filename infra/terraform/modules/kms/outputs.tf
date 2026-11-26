output "key_ring" {
  description = "Key ring id."
  value       = google_kms_key_ring.this.id
}

output "tenant_kek_id" {
  description = "Id of the key that wraps tenant data keys; services and permissions (phase 9) use it."
  value       = google_kms_crypto_key.tenant_kek.id
}
