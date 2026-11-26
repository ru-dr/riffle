# Phase 11: the load balancer's fixed IP and the Cloud Armor policy (diagram: Edge, WAF,
# Load balancer). The load balancer is created by the Gateway in phase 12. No domain yet, so
# no certificate: see modules/edge/README.md.

module "edge" {
  source = "../../modules/edge"

  project_id            = var.project_id
  name                  = local.name
  github_hook_ranges    = var.github_hook_ranges
  rate_limit_per_minute = var.edge_rate_limit_per_minute
  waf_preview           = var.edge_waf_preview
  labels                = local.labels

  depends_on = [module.project_services]
}
