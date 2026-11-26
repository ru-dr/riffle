output "name" {
  description = "Cluster name."
  value       = google_container_cluster.this.name
}

output "location" {
  description = "Cluster location (the region)."
  value       = google_container_cluster.this.location
}

output "dns_endpoint" {
  description = "Control plane DNS endpoint: gcloud container clusters get-credentials <name> --dns-endpoint."
  value       = google_container_cluster.this.control_plane_endpoints_config[0].dns_endpoint_config[0].endpoint
}

output "workload_pool" {
  description = "Workload Identity pool, for binding Kubernetes service accounts (phase 9)."
  value       = google_container_cluster.this.workload_identity_config[0].workload_pool
}

output "node_service_account" {
  description = "Email of the nodes' service account."
  value       = google_service_account.nodes.email
}
