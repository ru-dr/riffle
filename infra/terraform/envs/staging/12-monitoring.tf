# Phase 13 (Terraform part): uptime check and alerts in Cloud Monitoring (diagram: Uptime
# checks, Alertmanager, On-call). Loki, Tempo, Grafana and Alloy run in the cluster and are
# deployed by Argo CD from infra/k8s/apps/staging.

module "monitoring" {
  source = "../../modules/monitoring"

  project_id            = var.project_id
  name                  = local.name
  ingress_ip            = module.edge.ingress_ip
  alert_emails          = var.alert_emails
  max_delivery_attempts = jsondecode(file("${path.module}/../../../pubsub/topics.json")).dead_letter.max_delivery_attempts

  depends_on = [module.project_services]
}
