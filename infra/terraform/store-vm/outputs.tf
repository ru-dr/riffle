output "address" {
  value = google_compute_address.ip.address
}

output "sync_url" {
  description = "RIFFLE_SYNC_URL for the PCs (the lead's join codes carry it)."
  value       = "https://${local.domain}"
}

output "bucket" {
  value = google_storage_bucket.data.name
}

output "service_account" {
  value = google_service_account.vm.email
}

output "tunnel" {
  description = "Grafana on http://localhost:3000 and Airflow on http://localhost:8080 while this runs."
  value       = "gcloud compute ssh ${var.name} --project=${var.project} --zone=${var.zone} --tunnel-through-iap -- -N -L 3000:127.0.0.1:3000 -L 8080:127.0.0.1:8080"
}

output "airflow_passwords" {
  description = "Where the Airflow logins were written on the VM (root only)."
  value       = "sudo cat /opt/riffle/secrets/airflow-passwords.txt"
}
