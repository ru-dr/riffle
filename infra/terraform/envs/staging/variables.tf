variable "project_id" {
  description = "GCP project the platform runs in."
  type        = string
  default     = "riffle-infra-platform"
}

variable "region" {
  description = "Region for everything regional. us-east4 (Northern Virginia), as before."
  type        = string
  default     = "us-east4"
}

variable "environment" {
  description = "Environment name, used in resource names and labels."
  type        = string
  default     = "staging"

  validation {
    condition     = contains(["staging", "prod"], var.environment)
    error_message = "environment must be staging or prod."
  }
}

# Address plan. None of these may overlap.
variable "subnet_cidr" {
  description = "Node addresses."
  type        = string
  default     = "10.10.0.0/20"
}

variable "pods_cidr" {
  description = "Pod addresses (GKE secondary range)."
  type        = string
  default     = "10.20.0.0/16"
}

variable "services_cidr" {
  description = "Kubernetes service addresses (GKE secondary range)."
  type        = string
  default     = "10.30.0.0/20"
}

variable "private_services_cidr" {
  description = "Cloud SQL and Memorystore private addresses."
  type        = string
  default     = "10.40.0.0/16"
}

variable "cluster_release_channel" {
  description = "GKE release channel."
  type        = string
  default     = "REGULAR"
}

variable "cluster_deletion_protection" {
  description = "Refuse terraform destroy on the cluster. Off in staging so it can be torn down between demos."
  type        = bool
  default     = false
}

variable "db_tier" {
  description = "Cloud SQL machine type. db-custom-1-3840 = 1 vCPU, 3.75 GB."
  type        = string
  default     = "db-custom-1-3840"
}

variable "db_availability_type" {
  description = "ZONAL in staging (cheaper); REGIONAL in prod (standby in a second zone)."
  type        = string
  default     = "ZONAL"
}

variable "db_deletion_protection" {
  description = "Refuse deleting the database. Off in staging so it can be torn down between demos."
  type        = bool
  default     = false
}

variable "db_people" {
  description = "Team members who may log in to Postgres with their Google account (no password), keyed by a short name. Example: { divyam = { email = \"you@northeastern.edu\", database_roles = [] } }. Keep cloudsqlsuperuser for emergencies: it ignores tenant isolation."
  type = map(object({
    email          = string
    database_roles = list(string)
  }))
  default = {}
}

variable "redis_tier" {
  description = "BASIC in staging (one node); STANDARD_HA in prod (replica in a second zone)."
  type        = string
  default     = "BASIC"
}

variable "redis_memory_size_gb" {
  description = "Redis memory in GB."
  type        = number
  default     = 1
}

variable "redis_deletion_protection" {
  description = "Refuse deleting Redis. Off in staging so it can be torn down between demos."
  type        = bool
  default     = false
}

variable "storage_force_destroy" {
  description = "Let terraform destroy delete buckets that still hold objects. On in staging only."
  type        = bool
  default     = true
}

variable "log_retention_days" {
  description = "Days Loki keeps logs (the logs bucket deletes them one day later as a backstop)."
  type        = number
  default     = 30
}

variable "trace_retention_days" {
  description = "Days Tempo keeps traces (the traces bucket deletes them one day later as a backstop)."
  type        = number
  default     = 14
}

variable "github_repository" {
  description = "The only GitHub repository whose Actions runs are trusted (owner/name)."
  type        = string
  default     = "ru-dr/riffle"
}

variable "terraform_deploy_ref" {
  description = "The only git ref whose Actions runs may plan and apply Terraform."
  type        = string
  default     = "refs/heads/main"
}

variable "github_hook_ranges" {
  description = "GitHub's webhook sender ranges, from the hooks list of https://api.github.com/meta (checked 2026-11-25). Compare before each apply."
  type        = list(string)
  default = [
    "9.234.106.48/28",
    "9.234.98.160/28",
    "9.234.98.176/28",
    "192.30.252.0/22",
    "185.199.108.0/22",
    "140.82.112.0/20",
    "143.55.64.0/20",
    "2a0a:a440::/29",
    "2606:50c0::/32",
  ]
}

variable "edge_rate_limit_per_minute" {
  description = "Requests a minute one IP may send through the load balancer (GitHub's IPs are not limited)."
  type        = number
  default     = 300
}

variable "edge_waf_preview" {
  description = "Log-only mode for the SQL injection and XSS rules until their logs show no false positives."
  type        = bool
  default     = true
}

variable "alert_emails" {
  description = "Addresses alerts are emailed to. Empty: alerts only show in Cloud Monitoring."
  type        = list(string)
  default     = []
}

variable "db_connector_public_ip" {
  description = "Let the team reach Postgres from a laptop through the Cloud SQL Proxy (Google login, no authorised networks)."
  type        = bool
  default     = true
}

variable "team_members" {
  description = "People who may run and debug the platform (module team-access): user:<email> or group:<email>."
  type        = list(string)
  default     = []
}

variable "state_bucket" {
  description = "The Terraform state bucket (made by hand, see README); team members may read it."
  type        = string
  default     = "riffle-infra-platform-tfstate"
}
