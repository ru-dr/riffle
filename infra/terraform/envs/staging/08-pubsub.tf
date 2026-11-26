# Phase 8: Pub/Sub from infra/pubsub/topics.json (diagram: Message queue). Who may publish or
# read each topic is set per service in phase 9.

module "pubsub" {
  source = "../../modules/pubsub"

  project_id  = var.project_id
  region      = var.region
  name_prefix = local.name
  spec        = jsondecode(file("${path.module}/../../../pubsub/topics.json"))
  labels      = local.labels

  depends_on = [module.project_services]
}
