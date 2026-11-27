# Phase 9: one Google service account per service, linked to its Kubernetes service account
# (Workload Identity), with only what it needs. This file is the answer to "what may service X
# touch?". Namespaces are created in phase 12; names here must match them.
#
# Bucket roles: objectViewer = read, objectCreator = add new files only, objectUser = read,
# write and delete.

module "identity" {
  source = "../../modules/service-accounts"

  project_id    = var.project_id
  name_prefix   = local.name
  workload_pool = module.cluster.workload_pool

  topics        = module.pubsub.topics
  subscriptions = module.pubsub.subscriptions
  buckets       = module.storage.names
  secrets       = module.secrets.resource_ids
  tenant_kek_id = module.kms.tenant_kek_id

  services = {
    # ---- riffle-serving: the live path ----
    intake = {
      namespace   = "riffle-serving"
      description = "Webhook front door: verifies, dedupes, publishes"
      publishes   = ["pr-events", "installation-events", "outcome-events"]
      secrets     = ["github-webhook-secret", "redis-auth"]
    }
    scorer = {
      namespace   = "riffle-serving"
      description = "Scores each PR with the global and repo models"
      subscribes  = ["scorer-pr-events"]
      publishes   = ["score-results", "score-log"]
      buckets     = { models = "roles/storage.objectViewer" }
      secrets     = ["github-app-private-key"] # reads the PR from GitHub
      database    = true
    }
    explainer = {
      namespace   = "riffle-serving"
      description = "Turns a score into an explanation; needs nothing outside the cluster yet"
    }
    app-github = {
      namespace   = "riffle-serving"
      description = "Posts check runs; starts the tenant workflow on install events"
      subscribes  = ["app-score-results", "app-installation-events"]
      secrets     = ["github-app-private-key"]
      database    = true
    }
    app-dashboard = {
      namespace   = "riffle-serving"
      description = "Ranked-queue web page for reviewers"
      secrets     = ["github-app-client-secret", "dashboard-session-key", "redis-auth"]
      database    = true
    }

    # ---- riffle-workers: background work ----
    outcome-labeler = {
      namespace   = "riffle-workers"
      description = "Turns reverts, CI fails and hotfixes into labels"
      subscribes  = ["outcome-labeler-outcome-events"]
      secrets     = ["github-app-private-key"]
      database    = true
    }
    scorelog-writer = {
      namespace   = "riffle-workers"
      description = "Writes the score log to Parquet, encrypted per tenant (ADR 0024)"
      subscribes  = ["score-log-writer"]
      buckets     = { score-log = "roles/storage.objectCreator" } # adds files, never rewrites
      tenant_kek  = true
    }
    tenant-workflow = {
      namespace   = "riffle-workers"
      description = "Install, suspend and uninstall steps, history backfill (ADR 0025)"
      buckets = {
        score-log = "roles/storage.objectUser" # deletes a tenant's prefix on uninstall
        snapshots = "roles/storage.objectUser" # writes the backfill
      }
      secrets    = ["github-app-private-key"]
      tenant_kek = true # wraps each new tenant's data key
      database   = true
    }
    db-migrate = {
      namespace          = "riffle-workers"
      description        = "Runs infra/db/migrations before each deploy"
      database_superuser = true
    }

    # ---- riffle-ml: training ----
    retrain = {
      namespace   = "riffle-ml"
      description = "Decides when to train: new labels, drift, weekly"
      buckets     = { score-log = "roles/storage.objectViewer" }
      database    = true
    }
    trainer = {
      namespace   = "riffle-ml"
      description = "Global training and repo model fits, with the promotion gate"
      buckets = {
        score-log = "roles/storage.objectViewer"
        snapshots = "roles/storage.objectViewer"
        models    = "roles/storage.objectUser"
      }
      tenant_kek = true # decrypts tenants' score log and snapshots
      database   = true
    }
    mlflow = {
      namespace   = "riffle-ml"
      description = "Model registry: metadata in the mlflow database, files in the models bucket"
      buckets     = { models = "roles/storage.objectUser" }
      database    = true
    }

    # ---- observability and platform ----
    loki = {
      namespace   = "observability"
      description = "Stores logs in the loki bucket"
      buckets     = { loki = "roles/storage.objectUser" }
    }
    tempo = {
      namespace   = "observability"
      description = "Stores traces in the tempo bucket"
      buckets     = { tempo = "roles/storage.objectUser" }
    }
    grafana = {
      namespace     = "observability"
      description   = "Dashboards; reads metrics from Managed Prometheus through Cloud Monitoring"
      secrets       = ["grafana-admin"]
      project_roles = ["roles/monitoring.viewer"]
    }
    keda = {
      namespace     = "keda"
      ksa           = "keda-operator"
      description   = "Reads Pub/Sub backlog metrics to scale services"
      project_roles = ["roles/monitoring.viewer"]
    }
  }
}
