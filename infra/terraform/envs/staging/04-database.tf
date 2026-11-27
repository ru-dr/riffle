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
  connector_public_ip = var.db_connector_public_ip
  labels              = local.labels

  # Who may log in, all with Google identities, nobody with a password: the services from
  # phase 9 (09-identity.tf) and the team members in db_people.
  iam_users = merge(module.identity.database_users, var.db_people)

  # Private services access must exist before an instance can get a private IP.
  depends_on = [module.network]
}
