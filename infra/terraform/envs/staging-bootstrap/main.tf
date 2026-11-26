# Phase 12 (Terraform part): install Argo CD in the cluster and point it at
# infra/k8s/apps/staging. From then on Argo CD deploys everything in the cluster from git;
# Terraform never touches what runs inside the cluster again.
#
# A separate root from envs/staging because it needs the cluster to exist first: apply
# envs/staging, then this.

data "google_client_config" "this" {}

data "google_container_cluster" "this" {
  name     = var.cluster_name
  location = var.region
}

resource "helm_release" "argocd" {
  name             = "argocd"
  namespace        = "argocd"
  create_namespace = true
  repository       = "https://argoproj.github.io/argo-helm"
  chart            = "argo-cd"
  version          = var.argocd_chart_version

  values = [file("${path.module}/values/argocd.yaml")]

  wait    = true
  timeout = 900

  # Argo CD starts syncing at once; the namespaces and settings it expects must exist first.
  depends_on = [kubernetes_config_map_v1.settings]
}

# The root Application: it syncs every Application in apps_path (app of apps).
resource "helm_release" "root_app" {
  name       = "argocd-root"
  namespace  = "argocd"
  repository = "https://argoproj.github.io/argo-helm"
  chart      = "argocd-apps"
  version    = var.argocd_apps_chart_version

  values = [yamlencode({
    applications = {
      riffle-staging-root = {
        namespace = "argocd"
        project   = "default"
        source = {
          repoURL        = var.git_repo_url
          targetRevision = var.git_revision
          path           = var.apps_path
        }
        destination = {
          server    = "https://kubernetes.default.svc"
          namespace = "argocd"
        }
        syncPolicy = {
          automated = { prune = true, selfHeal = true }
        }
      }
    }
  })]

  depends_on = [helm_release.argocd]
}
