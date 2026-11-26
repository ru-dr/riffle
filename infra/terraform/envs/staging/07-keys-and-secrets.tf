# Phase 7: the tenant key-encryption key (ADR 0025) and the Secret Manager entries
# (diagram: Key vault). Who may use them is set per service in phase 9.

module "kms" {
  source = "../../modules/kms"

  project_id = var.project_id
  region     = var.region
  name       = local.name
  labels     = local.labels

  depends_on = [module.project_services]
}

# Values Terraform can make itself. Paste the webhook secret into the GitHub App's settings
# after the first apply (gcloud secrets versions access latest --secret=<id>).
resource "random_password" "github_webhook_secret" {
  length  = 40
  special = false
}

resource "random_password" "dashboard_session_key" {
  length  = 64
  special = false
}

module "secrets" {
  source = "../../modules/secrets"

  project_id = var.project_id
  region     = var.region
  name       = local.name
  labels     = local.labels

  secrets = {
    "github-app-private-key" = {
      purpose = "GitHub App private key (.pem), downloaded from the App's settings"
      set_by  = "hand"
    }
    "github-app-client-secret" = {
      purpose = "GitHub App OAuth client secret, for dashboard sign-in"
      set_by  = "hand"
    }
    "github-webhook-secret" = {
      purpose = "Signs every webhook; intake checks X-Hub-Signature-256 with it"
      set_by  = "terraform"
    }
    "dashboard-session-key" = {
      purpose = "Signs the dashboard's session cookies"
      set_by  = "terraform"
    }
    "redis-auth" = {
      purpose = "Memorystore AUTH string"
      set_by  = "terraform"
    }
  }

  values = {
    "github-webhook-secret" = random_password.github_webhook_secret.result
    "dashboard-session-key" = random_password.dashboard_session_key.result
    "redis-auth"            = module.redis.auth_string
  }

  depends_on = [module.project_services]
}
