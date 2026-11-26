# GitHub Actions access without keys (diagram: "CI/CD · GitHub Actions · Terraform"). GitHub
# gives each workflow run a short-lived signed token; Google checks it against this pool and
# lets the run act as one of two accounts for about an hour. No key is stored in GitHub.

# Pool ids stay reserved for 30 days after deletion; a random suffix lets staging be rebuilt.
resource "random_id" "suffix" {
  byte_length = 2
}

resource "google_iam_workload_identity_pool" "github" {
  project                   = var.project_id
  workload_identity_pool_id = "${var.name}-gh-${random_id.suffix.hex}"
  display_name              = "GitHub Actions"
  description               = "Workflow runs of ${var.repository}"
}

resource "google_iam_workload_identity_pool_provider" "github" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  display_name                       = "GitHub"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
    "attribute.actor"      = "assertion.actor"
    # Repository and branch in one value, so a binding can require both (see ci_terraform).
    "attribute.repo_ref" = "assertion.repository + '@' + assertion.ref"
  }

  # Tokens from any other repository are refused before any binding is looked at.
  attribute_condition = "assertion.repository == '${var.repository}'"
}

locals {
  principals = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}"
}

# ---- ci-images: build and push images, from any branch of the repository ----

resource "google_service_account" "ci_images" {
  project      = var.project_id
  account_id   = "${var.name}-ci-images"
  display_name = "CI: push images (${var.name})"
}

resource "google_service_account_iam_member" "ci_images" {
  service_account_id = google_service_account.ci_images.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "${local.principals}/attribute.repository/${var.repository}"
}

resource "google_artifact_registry_repository_iam_member" "ci_images" {
  project    = var.project_id
  location   = var.region
  repository = var.artifact_repository
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.ci_images.email}"
}

# ---- ci-terraform: plan and apply, only from one branch (main by default) ----

resource "google_service_account" "ci_terraform" {
  project      = var.project_id
  account_id   = "${var.name}-ci-terraform"
  display_name = "CI: Terraform on ${var.terraform_ref} (${var.name})"
}

resource "google_service_account_iam_member" "ci_terraform" {
  service_account_id = google_service_account.ci_terraform.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "${local.principals}/attribute.repo_ref/${var.repository}@${var.terraform_ref}"
}

resource "google_project_iam_member" "ci_terraform" {
  for_each = toset(var.terraform_roles)

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.ci_terraform.email}"
}
