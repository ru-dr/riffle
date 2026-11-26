locals {
  # Prefix for every resource name: riffle-staging-...
  name = "riffle-${var.environment}"

  # Put on every resource that supports labels, so cost and ownership can be filtered.
  labels = {
    app         = "riffle"
    environment = var.environment
    managed_by  = "terraform"
  }

  # Every Google API the platform uses. A phase that needs a new one adds it here.
  services = [
    "cloudresourcemanager.googleapis.com",
    "serviceusage.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "sts.googleapis.com",
    "compute.googleapis.com",
    "servicenetworking.googleapis.com",
    "container.googleapis.com",
    "sqladmin.googleapis.com",
    "redis.googleapis.com",
    "pubsub.googleapis.com",
    "storage.googleapis.com",
    "cloudkms.googleapis.com",
    "secretmanager.googleapis.com",
    "artifactregistry.googleapis.com",
    "iap.googleapis.com",
    "monitoring.googleapis.com",
    "logging.googleapis.com",
  ]
}
