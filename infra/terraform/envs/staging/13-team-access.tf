# What people on the team may do: kubectl, secrets, the database through the Cloud SQL Proxy,
# metrics, logs and Terraform state, from their laptops (infra/terraform/README.md, Access).
# For the database, members also need an entry in db_people.

module "team_access" {
  source = "../../modules/team-access"

  project_id   = var.project_id
  members      = var.team_members
  state_bucket = var.state_bucket

  depends_on = [module.project_services]
}
