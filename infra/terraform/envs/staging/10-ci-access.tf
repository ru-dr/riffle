# Phase 10: the container registry and GitHub Actions access without keys
# (diagram: CI/CD, Container registry).

module "artifact_registry" {
  source = "../../modules/artifact-registry"

  project_id = var.project_id
  region     = var.region
  name       = local.name
  labels     = local.labels

  depends_on = [module.project_services]
}

module "github_ci" {
  source = "../../modules/github-oidc"

  project_id          = var.project_id
  region              = var.region
  name                = local.name
  repository          = var.github_repository
  terraform_ref       = var.terraform_deploy_ref
  artifact_repository = module.artifact_registry.repository

  # What Terraform needs to manage everything in envs/staging. Editor creates resources; the
  # admin roles add what Editor lacks: setting permissions on projects, service accounts,
  # topics, buckets, secrets and keys.
  terraform_roles = [
    "roles/editor",
    "roles/resourcemanager.projectIamAdmin",
    "roles/iam.serviceAccountAdmin",
    "roles/iam.workloadIdentityPoolAdmin",
    "roles/pubsub.admin",
    "roles/storage.admin",
    "roles/secretmanager.admin",
    "roles/cloudkms.admin",
    "roles/container.admin",
  ]

  depends_on = [module.project_services]
}
