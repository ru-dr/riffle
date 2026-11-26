terraform {
  # CI runs 1.9 (.github/workflows/ci.yml, job terraform).
  required_version = ">= 1.9"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 8.12"
    }
  }

  # State lives in a GCS bucket created once by hand (see infra/terraform/README.md).
  # Its name goes in backend.hcl, which is gitignored: terraform init -backend-config=backend.hcl
  backend "gcs" {}
}

provider "google" {
  project = var.project_id
  region  = var.region

  default_labels = local.labels
}
