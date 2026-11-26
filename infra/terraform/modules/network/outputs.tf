output "network_id" {
  description = "VPC id, for GKE, Cloud SQL and Memorystore."
  value       = google_compute_network.this.id
}

output "network_name" {
  description = "VPC name."
  value       = google_compute_network.this.name
}

output "subnet_id" {
  description = "The cluster's subnet."
  value       = google_compute_subnetwork.this.id
}

output "pods_range_name" {
  description = "Secondary range name for pods."
  value       = "pods"
}

output "services_range_name" {
  description = "Secondary range name for services."
  value       = "services"
}

output "private_services_connection" {
  description = "Depend on this before creating Cloud SQL or Memorystore with a private IP."
  value       = google_service_networking_connection.private_services.id
}

output "egress_ip" {
  description = "The one address all outbound traffic leaves from."
  value       = google_compute_address.egress.address
}
