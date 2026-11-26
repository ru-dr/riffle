output "egress_ip" {
  description = "The one IP all outbound traffic (GitHub API, check runs) leaves from."
  value       = module.network.egress_ip
}

output "network_name" {
  description = "VPC name."
  value       = module.network.network_name
}

output "cluster_name" {
  description = "GKE cluster name."
  value       = module.cluster.name
}

output "cluster_dns_endpoint" {
  description = "Control plane DNS endpoint, for kubectl via gcloud --dns-endpoint."
  value       = module.cluster.dns_endpoint
}

output "db_connection_name" {
  description = "Cloud SQL connection name (project:region:instance), for the connector in each pod."
  value       = module.database.connection_name
}

output "redis_host" {
  description = "Redis private IP (port 6378, TLS)."
  value       = module.redis.host
}
