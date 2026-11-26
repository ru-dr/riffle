# Phase 2: the private network, private access for Cloud SQL and Memorystore, and Cloud NAT
# with one fixed egress IP (diagram: NAT gateway).

module "network" {
  source = "../../modules/network"

  project_id            = var.project_id
  region                = var.region
  name                  = local.name
  subnet_cidr           = var.subnet_cidr
  pods_cidr             = var.pods_cidr
  services_cidr         = var.services_cidr
  private_services_cidr = var.private_services_cidr

  depends_on = [module.project_services]
}
