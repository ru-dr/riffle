# Phase 3: the GKE Autopilot cluster, regional, private nodes, Workload Identity
# (diagram: Kubernetes cluster).

module "cluster" {
  source = "../../modules/gke-autopilot"

  project_id          = var.project_id
  region              = var.region
  name                = local.name
  network_id          = module.network.network_id
  subnet_id           = module.network.subnet_id
  pods_range_name     = module.network.pods_range_name
  services_range_name = module.network.services_range_name
  release_channel     = var.cluster_release_channel
  deletion_protection = var.cluster_deletion_protection

  depends_on = [module.project_services]
}
