# What people on the team may do, written down instead of clicked in the console. Every member
# gets the same roles: enough to run and debug the platform from a laptop (kubectl, secrets,
# the database through the Cloud SQL Proxy, metrics, logs, Terraform state), not to change
# IAM or delete things. Applying Terraform itself needs more and is done by the project
# owner or by CI on main (github-oidc module).

locals {
  grants = { for pair in setproduct(var.members, var.roles) : "${pair[0]} ${pair[1]}" => { member = pair[0], role = pair[1] } }
}

resource "google_project_iam_member" "this" {
  for_each = local.grants

  project = var.project_id
  role    = each.value.role
  member  = each.value.member
}

# Terraform's state holds the values Terraform generated (Redis AUTH, webhook secret, session
# and Grafana keys), so only the team may read it.
resource "google_storage_bucket_iam_member" "state" {
  for_each = var.state_bucket == "" ? toset([]) : toset(var.members)

  bucket = var.state_bucket
  role   = "roles/storage.objectViewer"
  member = each.value
}
