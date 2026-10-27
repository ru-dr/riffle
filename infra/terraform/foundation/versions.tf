terraform {
  required_version = ">= 1.9"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.0"
    }
  }

  # Partial configuration: the state bucket name is project specific, so it lives
  # in backend.hcl (gitignored) and is passed with
  #   terraform init -backend-config=backend.hcl
  # See backend.hcl.example. The bucket is created once by hand (see ../README.md);
  # Terraform cannot create the bucket that stores its own state.
  backend "gcs" {
    prefix = "foundation"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region

  # Bill API calls to the project, not to the gcloud client's own project.
  # Needed for the Billing Budgets API with user credentials.
  user_project_override = true
  billing_project       = var.project_id
}
