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
