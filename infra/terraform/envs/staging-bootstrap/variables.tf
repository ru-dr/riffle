variable "project_id" {
  description = "GCP project the cluster is in."
  type        = string
  default     = "riffle-infra-platform"
}

variable "region" {
  description = "Region of the cluster."
  type        = string
  default     = "us-east4"
}

variable "cluster_name" {
  description = "The cluster envs/staging created (output cluster_name)."
  type        = string
  default     = "riffle-staging"
}

variable "git_repo_url" {
  description = "Repository Argo CD deploys from (public, so no credentials)."
  type        = string
  default     = "https://github.com/ru-dr/riffle.git"
}

variable "git_revision" {
  description = "Branch, tag or commit Argo CD follows."
  type        = string
  default     = "main"
}

variable "apps_path" {
  description = "Folder of Argo CD Applications for this environment (app of apps)."
  type        = string
  default     = "infra/k8s/apps/staging"
}

variable "argocd_chart_version" {
  description = "argo-cd Helm chart version (argoproj.github.io/argo-helm)."
  type        = string
  default     = "10.20.0"
}

variable "argocd_apps_chart_version" {
  description = "argocd-apps Helm chart version, which creates the root Application."
  type        = string
  default     = "2.0.10"
}

variable "state_bucket" {
  description = "The bucket holding envs/staging's state, read for its outputs."
  type        = string
  default     = "riffle-infra-platform-tfstate"
}
