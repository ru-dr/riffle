output "host" {
  description = "Private IP of the instance."
  value       = google_redis_instance.this.host
}

output "port" {
  description = "Port (TLS)."
  value       = google_redis_instance.this.port
}

output "auth_string" {
  description = "The AUTH password. Stored in Secret Manager in phase 7; never print it."
  value       = google_redis_instance.this.auth_string
  sensitive   = true
}

output "server_ca_cert" {
  description = "CA certificate clients use to check the server's TLS certificate (not secret)."
  value       = google_redis_instance.this.server_ca_certs[0].cert
}
