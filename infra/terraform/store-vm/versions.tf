terraform {
  required_version = ">= 1.6"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }

  # State is remote and never applied from a laptop by hand (../README.md). The bucket and
  # prefix come from `terraform init -backend-config=...`, so no project name is committed.
  backend "gcs" {}
}

provider "google" {
  project = var.project
  region  = var.region
  zone    = var.zone
}
