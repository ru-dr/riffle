# The team store VM (ADR 0015) with run tracking (ADR 0017): the store and its pages behind Caddy
# (HTTPS), Loki + Grafana, and Airflow, all from the Compose files in infra/docker.
# Grafana and Airflow listen on the VM's loopback only; read them through an SSH tunnel (outputs.tf).

locals {
  domain = var.domain != "" ? var.domain : "${replace(google_compute_address.ip.address, ".", "-")}.sslip.io"
  apis   = ["compute.googleapis.com", "storage.googleapis.com", "iam.googleapis.com", "iap.googleapis.com", "secretmanager.googleapis.com"]
}

resource "google_project_service" "api" {
  for_each           = toset(local.apis)
  service            = each.value
  disable_on_destroy = false
}

resource "google_service_account" "vm" {
  account_id   = var.name
  display_name = "Riffle team store VM"
  depends_on   = [google_project_service.api]
}

resource "google_storage_bucket" "data" {
  name                        = var.bucket
  location                    = var.bucket_location
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  lifecycle {
    prevent_destroy = true # mined datasets are weeks of work
  }

  depends_on = [google_project_service.api]
}

resource "google_storage_bucket_iam_member" "vm_objects" {
  bucket = google_storage_bucket.data.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.vm.email}"
}

# the VM reads the repo token, if there is one, with its own identity
resource "google_secret_manager_secret_iam_member" "repo_token" {
  count     = var.repo_token_secret == "" ? 0 : 1
  secret_id = var.repo_token_secret
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.vm.email}"
}

resource "google_compute_address" "ip" {
  name       = "${var.name}-ip"
  region     = var.region
  depends_on = [google_project_service.api]
}

# 80 and 443 are the point (the pages and the certificate). Nothing else is open to the internet.
resource "google_compute_firewall" "web" {
  name          = "${var.name}-web"
  network       = "default"
  direction     = "INGRESS"
  target_tags   = [var.name]
  source_ranges = ["0.0.0.0/0"]

  allow {
    protocol = "tcp"
    ports    = ["80", "443"]
  }
}

# ssh only through IAP (Google's range), so there is no port 22 on the internet
resource "google_compute_firewall" "iap_ssh" {
  name          = "${var.name}-iap-ssh"
  network       = "default"
  direction     = "INGRESS"
  target_tags   = [var.name]
  source_ranges = ["35.235.240.0/20"]

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}

resource "google_compute_instance" "vm" {
  name                      = var.name
  machine_type              = var.machine_type
  zone                      = var.zone
  tags                      = [var.name]
  allow_stopping_for_update = true

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
      size  = var.disk_gb
      type  = "pd-balanced"
    }
  }

  network_interface {
    network = "default"
    access_config {
      nat_ip = google_compute_address.ip.address
    }
  }

  service_account {
    email  = google_service_account.vm.email # no key file: the VM's own identity reaches the bucket
    scopes = ["cloud-platform"]
  }

  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }

  metadata = {
    enable-oslogin = "TRUE"
  }

  metadata_startup_script = templatefile("${path.module}/startup.sh.tftpl", {
    project           = var.project
    bucket            = google_storage_bucket.data.name
    domain            = local.domain
    repo_url          = var.repo_url
    repo_ref          = var.repo_ref
    repo_token_secret = var.repo_token_secret
    enable_logs       = var.enable_logs
    enable_airflow    = var.enable_airflow
    airflow_users     = join(",", var.airflow_users)
  })

  lifecycle {
    prevent_destroy = true # the store's tokens, index and traces live on this disk: a plan that replaces the VM must stop here
    ignore_changes = [
      boot_disk[0].initialize_params[0].image, # a newer Debian image must not rebuild the VM
      boot_disk[0].initialize_params[0].type,  # nor must a VM made by gcp/up.sh, which has another disk type and size;
      boot_disk[0].initialize_params[0].size,  # resize the disk by hand (README)
    ]
  }

  depends_on = [google_storage_bucket_iam_member.vm_objects]
}
