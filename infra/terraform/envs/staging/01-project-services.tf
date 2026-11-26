# Phase 1: turn on the Google APIs. Everything else waits for this.

module "project_services" {
  source = "../../modules/project-services"

  project_id = var.project_id
  services   = local.services
}
