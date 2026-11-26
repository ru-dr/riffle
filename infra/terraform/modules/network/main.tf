# The private network everything runs in (diagram: the "Any cloud" boundary and the NAT
# gateway). Nothing here has a public address except the NAT's one egress IP.

resource "google_compute_network" "this" {
  project                 = var.project_id
  name                    = var.name
  auto_create_subnetworks = false
  routing_mode            = "REGIONAL"
}

# One subnet for the cluster. GKE takes pod and service addresses from the two secondary
# ranges, so they never collide with node addresses.
resource "google_compute_subnetwork" "this" {
  project                  = var.project_id
  name                     = "${var.name}-${var.region}"
  region                   = var.region
  network                  = google_compute_network.this.id
  ip_cidr_range            = var.subnet_cidr
  private_ip_google_access = true # reach Google APIs (Pub/Sub, Storage, ...) without a public IP

  secondary_ip_range {
    range_name    = "pods"
    ip_cidr_range = var.pods_cidr
  }
  secondary_ip_range {
    range_name    = "services"
    ip_cidr_range = var.services_cidr
  }
}

# Private services access: Cloud SQL and Memorystore get private addresses inside this
# range, reachable only from the VPC.
resource "google_compute_global_address" "private_services" {
  project       = var.project_id
  name          = "${var.name}-private-services"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  address       = split("/", var.private_services_cidr)[0]
  prefix_length = tonumber(split("/", var.private_services_cidr)[1])
  network       = google_compute_network.this.id
}

resource "google_service_networking_connection" "private_services" {
  network                 = google_compute_network.this.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_services.name]
}

# Cloud NAT with one reserved IP: every outbound call (GitHub API, check runs) leaves from
# this address, which customers can allow-list.
resource "google_compute_address" "egress" {
  project = var.project_id
  name    = "${var.name}-egress"
  region  = var.region
}

resource "google_compute_router" "this" {
  project = var.project_id
  name    = "${var.name}-router"
  region  = var.region
  network = google_compute_network.this.id
}

resource "google_compute_router_nat" "this" {
  project                            = var.project_id
  name                               = "${var.name}-nat"
  region                             = var.region
  router                             = google_compute_router.this.name
  nat_ip_allocate_option             = "MANUAL_ONLY"
  nat_ips                            = [google_compute_address.egress.self_link]
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"

  log_config {
    enable = true
    filter = "ERRORS_ONLY"
  }
}
