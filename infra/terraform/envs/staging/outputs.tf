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

output "pubsub_topics" {
  description = "Pub/Sub topic name per topics.json name."
  value       = module.pubsub.topics
}

output "pubsub_dead_letter_subscriptions" {
  description = "Where failed messages wait, per topic."
  value       = module.pubsub.dead_letter_subscriptions
}

output "service_accounts" {
  description = "Google service account email per service; each Kubernetes service account is annotated with its own."
  value       = module.identity.emails
}

output "image_registry" {
  description = "Image prefix for every service: <this>/<service>:<tag>."
  value       = module.artifact_registry.url
}

output "github_actions" {
  description = "Values for google-github-actions/auth (repository variables; none is secret)."
  value = {
    workload_identity_provider = module.github_ci.workload_identity_provider
    ci_images_service_account  = module.github_ci.ci_images_email
    ci_terraform_account       = module.github_ci.ci_terraform_email
  }
}

output "ingress_ip" {
  description = "Public IP of the load balancer. The GitHub App's webhook URL is http://<this>/webhook until there is a domain."
  value       = module.edge.ingress_ip
}
