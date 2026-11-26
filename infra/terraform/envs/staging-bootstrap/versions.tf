terraform {
  # CI runs 1.9 (.github/workflows/ci.yml, job terraform).
  required_version = ">= 1.9"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 8.12"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 3.3"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 3.5"
    }
  }

  # Same state bucket as envs/staging, its own prefix: backend.hcl.example.
  backend "gcs" {}
}

provider "google" {
  project = var.project_id
  region  = var.region
}

locals {
  cluster_host = "https://${data.google_container_cluster.this.control_plane_endpoints_config[0].dns_endpoint_config[0].endpoint}"
}

# Both talk to the cluster through its DNS endpoint with the caller's Google login (phase 3): no
# kubeconfig, no IP allow-list. The endpoint's certificate is publicly trusted.
provider "helm" {
  kubernetes = {
    host  = local.cluster_host
    token = data.google_client_config.this.access_token
  }
}

provider "kubernetes" {
  host  = local.cluster_host
  token = data.google_client_config.this.access_token
}
