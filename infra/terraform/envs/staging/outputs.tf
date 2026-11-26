output "egress_ip" {
  description = "The one IP all outbound traffic (GitHub API, check runs) leaves from."
  value       = module.network.egress_ip
}

output "network_name" {
  description = "VPC name."
  value       = module.network.network_name
}
