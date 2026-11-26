# The namespaces Riffle runs in, and in each a ConfigMap of platform settings that only
# Terraform knows (the Cloud SQL connection name has a random suffix, topic and bucket names
# have the environment prefix). Services and Jobs read them with envFrom, so no manifest in
# infra/k8s hard-codes an environment's resource names.
#
# Everything else in these namespaces (quotas, network rules, service accounts, ...) is
# deployed by Argo CD from infra/k8s/platform.

data "terraform_remote_state" "platform" {
  backend = "gcs"
  config = {
    bucket = var.state_bucket
    prefix = "envs/staging"
  }
}

locals {
  platform = data.terraform_remote_state.platform.outputs

  # tier: which group of the cluster the namespace is (see infra/k8s/README.md).
  # gateway-access: routes in this namespace may attach to the shared Gateway.
  namespaces = {
    "riffle-gateway" = { tier = "edge", gateway_access = "false" }
    "riffle-serving" = { tier = "serving", gateway_access = "true" }
    "riffle-workers" = { tier = "workers", gateway_access = "false" }
    "riffle-ml"      = { tier = "ml", gateway_access = "false" }
    "observability"  = { tier = "observability", gateway_access = "false" }
  }

  # TOPIC_PR_EVENTS, SUBSCRIPTION_SCORER_PR_EVENTS, BUCKET_SCORE_LOG, ... distinct(): the same
  # short name can be both a topic and a bucket (score-log).
  env_name = { for k in distinct(concat(keys(local.platform.pubsub_topics), keys(local.platform.pubsub_subscriptions), keys(local.platform.buckets))) : k => upper(replace(k, "-", "_")) }

  settings = merge(
    {
      GCP_PROJECT_ID     = local.platform.project_id
      GCP_REGION         = local.platform.region
      CLUSTER_NAME       = var.cluster_name
      DB_CONNECTION_NAME = local.platform.db_connection_name
      DB_NAME            = "riffle"
      MLFLOW_DB_NAME     = "mlflow"
      REDIS_HOST         = local.platform.redis_host
      REDIS_PORT         = tostring(local.platform.redis_port)
      REDIS_CA_CERT      = local.platform.redis_ca_cert
      TENANT_KEK_ID      = local.platform.tenant_kek_id
      IMAGE_REGISTRY     = local.platform.image_registry
      PUBSUB_PROJECT_ID  = local.platform.project_id
    },
    { for k, v in local.platform.pubsub_topics : "TOPIC_${local.env_name[k]}" => v },
    { for k, v in local.platform.pubsub_subscriptions : "SUBSCRIPTION_${local.env_name[k]}" => v },
    { for k, v in local.platform.buckets : "BUCKET_${local.env_name[k]}" => v },
  )
}

resource "kubernetes_namespace_v1" "this" {
  for_each = local.namespaces

  metadata {
    name = each.key
    labels = {
      "app.kubernetes.io/part-of" = "riffle"
      "riffle.dev/tier"           = each.value.tier
      "riffle.dev/gateway-access" = each.value.gateway_access
    }
  }
}

resource "kubernetes_config_map_v1" "settings" {
  for_each = local.namespaces

  metadata {
    name      = "riffle-platform-settings"
    namespace = kubernetes_namespace_v1.this[each.key].metadata[0].name
    labels = {
      "app.kubernetes.io/part-of"    = "riffle"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }

  data = local.settings
}
