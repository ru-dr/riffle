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

output "buckets" {
  description = "Bucket name per purpose."
  value       = module.storage.names
}

output "tenant_kek_id" {
  description = "KMS key that wraps every tenant's data key (ADR 0025)."
  value       = module.kms.tenant_kek_id
}

output "secrets" {
  description = "Secret Manager id per secret."
  value       = module.secrets.ids
}

output "secrets_to_fill_by_hand" {
  description = "Add a value to each after the first apply: gcloud secrets versions add <id> --data-file=<file>."
  value       = module.secrets.set_by_hand
}
