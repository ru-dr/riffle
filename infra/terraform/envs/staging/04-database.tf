# Phase 4: Cloud SQL Postgres 17, private IP, IAM logins only (diagram: Postgres, and the
# model registry's metadata). Logins for the services are added in phase 9.

module "database" {
  source = "../../modules/cloud-sql"

  project_id          = var.project_id
  region              = var.region
  name                = local.name
  network_id          = module.network.network_id
  tier                = var.db_tier
  availability_type   = var.db_availability_type
  deletion_protection = var.db_deletion_protection
  labels              = local.labels

  # People who may log in (for debugging), with their Google account; services are added in
  # phase 9. Nobody gets a password.
  iam_users = var.db_people

  # Private services access must exist before an instance can get a private IP.
  depends_on = [module.network]
}
