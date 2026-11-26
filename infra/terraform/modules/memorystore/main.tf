# Redis (diagram: "Redis · dedupe keys · cache · sessions"). Holds only short-lived data:
# intake's delivery claims, caches and dashboard sessions. Losing it costs at most a duplicate
# score, which the scorer's unique delivery_id stops, so there is no persistence.

resource "google_redis_instance" "this" {
  project        = var.project_id
  name           = var.name
  region         = var.region
  tier           = var.tier # STANDARD_HA = replica in a second zone with automatic failover
  memory_size_gb = var.memory_size_gb
  redis_version  = var.redis_version

  # A private address from the network module's private services access range, so it is
  # reachable only inside the VPC.
  authorized_network = var.network_id
  connect_mode       = "PRIVATE_SERVICE_ACCESS"

  # Clients need the AUTH string (kept in Secret Manager, phase 7) and talk TLS, checking the
  # server against server_ca_certs.
  auth_enabled            = true
  transit_encryption_mode = "SERVER_AUTHENTICATION"

  redis_configs = {
    # When memory is full, drop the least recently used key that has an expiry. Every key
    # Riffle writes has one, so nothing without a TTL is ever evicted by surprise.
    "maxmemory-policy" = "volatile-lru"
  }

  # Sunday 05:00 UTC, inside the weekend window the cluster and the database use.
  maintenance_policy {
    weekly_maintenance_window {
      day = "SUNDAY"
      start_time {
        hours   = 5
        minutes = 0
      }
    }
  }

  deletion_protection = var.deletion_protection
  labels              = var.labels
}
