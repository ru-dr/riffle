# Postgres (diagram: "Postgres", and the Model registry's metadata). Private IP only, no
# passwords: every login is a Google identity, through the Cloud SQL connector.

# Cloud SQL keeps a deleted instance's name reserved for about a week, so a fixed name would
# block re-creating staging after a destroy. A random suffix avoids that.
resource "random_id" "suffix" {
  byte_length = 2
}

resource "google_sql_database_instance" "this" {
  project             = var.project_id
  name                = "${var.name}-${random_id.suffix.hex}"
  region              = var.region
  database_version    = var.database_version
  deletion_protection = var.deletion_protection # Terraform refuses to destroy it

  settings {
    # Postgres 16+ defaults to Enterprise Plus, which costs several times more.
    edition           = "ENTERPRISE"
    tier              = var.tier
    availability_type = var.availability_type # REGIONAL = standby in a second zone, automatic failover

    disk_type             = "PD_SSD"
    disk_size             = var.disk_size_gb
    disk_autoresize       = true
    disk_autoresize_limit = var.disk_autoresize_limit_gb

    deletion_protection_enabled = var.deletion_protection # GCP refuses to delete it, even outside Terraform
    retain_backups_on_delete    = var.deletion_protection

    # Every connection must come through the Cloud SQL connector (the Auth Proxy sidecar or a
    # language connector): TLS and Google IAM on every login, no IPs or passwords to manage.
    connector_enforcement = "REQUIRED"

    ip_configuration {
      # Private IP only: reachable from inside the VPC (pods), never from the internet. People
      # connect through a proxy pod in the cluster (infra/k8s/tools/db-proxy.yaml).
      ipv4_enabled    = false
      private_network = var.network_id
      ssl_mode        = "ENCRYPTED_ONLY"
    }

    backup_configuration {
      enabled                        = true
      start_time                     = "03:00"             # UTC
      location                       = var.backup_location # multi-region: backups survive a region outage
      point_in_time_recovery_enabled = true
      transaction_log_retention_days = 7

      backup_retention_settings {
        retained_backups = var.backups_retained
      }
    }

    # Sunday 04:00 UTC, after the cluster's weekend window has started.
    maintenance_window {
      day          = 7
      hour         = 4
      update_track = "stable"
    }

    insights_config {
      query_insights_enabled = true
    }

    database_flags {
      name  = "cloudsql.iam_authentication" # logins with Google identities
      value = "on"
    }
    database_flags {
      name  = "log_min_duration_statement" # log queries slower than 500 ms
      value = "500"
    }
    database_flags {
      name  = "log_lock_waits"
      value = "on"
    }

    user_labels = var.labels
  }
}

resource "google_sql_database" "this" {
  for_each = toset(var.databases)

  project  = var.project_id
  instance = google_sql_database_instance.this.name
  name     = each.value
}

# Google identities that may log in; nobody has a password. A service account's database user
# is its email without ".gserviceaccount.com"; a person's is their full email. The migration
# account gets cloudsqlsuperuser so it can create the riffle_app role; services get riffle_app
# once the first migration has made it (see the README).
resource "google_sql_user" "iam" {
  for_each = var.iam_users

  project        = var.project_id
  instance       = google_sql_database_instance.this.name
  name           = endswith(each.value.email, ".gserviceaccount.com") ? trimsuffix(each.value.email, ".gserviceaccount.com") : each.value.email
  type           = endswith(each.value.email, ".gserviceaccount.com") ? "CLOUD_IAM_SERVICE_ACCOUNT" : "CLOUD_IAM_USER"
  database_roles = each.value.database_roles
}
