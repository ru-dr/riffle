# The Kubernetes cluster (diagram: "Kubernetes cluster"). Autopilot: Google runs the nodes and
# bills per pod, so there are no node pools to size or patch here.

# Nodes run as this account, not the default Compute Engine one (which has Editor on the
# whole project). It may only write logs and metrics and pull images.
resource "google_service_account" "nodes" {
  project      = var.project_id
  account_id   = "${var.name}-nodes"
  display_name = "GKE nodes (${var.name})"
}

resource "google_project_iam_member" "nodes" {
  for_each = toset([
    "roles/container.defaultNodeServiceAccount", # logs, metrics, metadata
    "roles/artifactregistry.reader",             # pull our images
  ])

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.nodes.email}"
}

resource "google_container_cluster" "this" {
  project  = var.project_id
  name     = var.name
  location = var.region # regional: the control plane and nodes span the region's zones

  enable_autopilot    = true
  deletion_protection = var.deletion_protection

  network         = var.network_id
  subnetwork      = var.subnet_id
  networking_mode = "VPC_NATIVE"

  ip_allocation_policy {
    cluster_secondary_range_name  = var.pods_range_name
    services_secondary_range_name = var.services_range_name
  }

  # Nodes get no public IP (they go out through Cloud NAT), and the control plane's IP
  # endpoint is private. People and CI reach the control plane through its DNS endpoint,
  # which checks Google IAM on every call, so there is no IP allow-list to keep up to date.
  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = true
  }

  control_plane_endpoints_config {
    dns_endpoint_config {
      allow_external_traffic = true
    }
    ip_endpoints_config {
      enabled = true
    }
  }

  cluster_autoscaling {
    auto_provisioning_defaults {
      service_account = google_service_account.nodes.email
      oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"] # IAM decides, not scopes
    }
  }

  # Pods act as Google service accounts (phase 9), never with keys.
  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }

  release_channel {
    channel = var.release_channel
  }

  # The Gateway API creates the load balancer for /webhook and /app (phase 12).
  gateway_api_config {
    channel = "CHANNEL_STANDARD"
  }

  # Google Managed Prometheus collects riffle_* metrics (diagram: Prometheus).
  monitoring_config {
    managed_prometheus {
      enabled = true
    }
  }

  # Cost per namespace and label in billing reports.
  cost_management_config {
    enabled = true
  }

  # Upgrades only at the weekend, UTC night. Autopilot needs at least 48 hours of windows in
  # any 32 days; this gives 64.
  maintenance_policy {
    recurring_window {
      start_time = "2026-01-03T00:00:00Z"
      end_time   = "2026-01-03T08:00:00Z"
      recurrence = "FREQ=WEEKLY;BYDAY=SA,SU"
    }
  }

  depends_on = [google_project_iam_member.nodes]
}
