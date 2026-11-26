output "ingress_ip" {
  description = "The public IP of the load balancer: GitHub's webhook URL is http(s)://<this IP or a name for it>/webhook."
  value       = google_compute_global_address.ingress.address
}

output "ingress_ip_name" {
  description = "Name of the IP, which the Gateway (phase 12) asks for in spec.addresses."
  value       = google_compute_global_address.ingress.name
}

output "security_policy_name" {
  description = "Cloud Armor policy name, attached to the backends with a GCPBackendPolicy (phase 12)."
  value       = google_compute_security_policy.edge.name
}
