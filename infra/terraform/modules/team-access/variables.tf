variable "project_id" {
  description = "GCP project."
  type        = string
}

variable "members" {
  description = "Who gets access, as IAM members: user:you@northeastern.edu or group:riffle-devops@... A group is easier to keep up to date."
  type        = list(string)

  validation {
    condition     = alltrue([for m in var.members : can(regex("^(user|group):[^@\\s]+@[^@\\s]+\\.[a-z]+$", m))])
    error_message = "Each member must be user:<email> or group:<email>."
  }
}

variable "roles" {
  description = "Project roles every member gets."
  type        = list(string)
  default = [
    "roles/container.developer",          # kubectl: read pods, logs and Secrets; port-forward
    "roles/secretmanager.secretAccessor", # read secret values (gcloud secrets versions access)
    "roles/secretmanager.viewer",         # list secrets
    "roles/cloudsql.client",              # connect through the Cloud SQL Proxy
    "roles/cloudsql.instanceUser",        # log in with a Google identity (no password)
    "roles/monitoring.viewer",            # metrics and alerts (Managed Prometheus, Cloud Monitoring)
    "roles/logging.viewer",               # Cloud Logging
    "roles/pubsub.viewer",                # see topics, subscriptions and dead letters
    "roles/artifactregistry.reader",      # pull images
  ]
}

variable "state_bucket" {
  description = "Terraform state bucket the members may read (it holds generated secret values). Empty: none."
  type        = string
  default     = ""
}
