# Phase 5: Memorystore Redis 7.2, private, AUTH and TLS on (diagram: Redis). Its AUTH string
# goes to Secret Manager in phase 7.

module "redis" {
  source = "../../modules/memorystore"

  project_id          = var.project_id
  region              = var.region
  name                = local.name
  network_id          = module.network.network_id
  tier                = var.redis_tier
  memory_size_gb      = var.redis_memory_size_gb
  deletion_protection = var.redis_deletion_protection
  labels              = local.labels

  # Private services access must exist before the instance can get a private IP.
  depends_on = [module.network]
}
