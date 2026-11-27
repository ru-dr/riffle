variable "project_id" {
  description = "GCP project."
  type        = string
}

variable "region" {
  description = "Region of the instance."
  type        = string
}

variable "name" {
  description = "Instance name prefix; a random suffix is added."
  type        = string
}

variable "network_id" {
  description = "VPC id. Private services access must already exist on it (network module)."
  type        = string
}

variable "database_version" {
  description = "Postgres version."
  type        = string
  default     = "POSTGRES_17"

  validation {
    condition     = can(regex("^POSTGRES_[0-9]+$", var.database_version))
    error_message = "database_version must be POSTGRES_<major>."
  }
}

variable "tier" {
  description = "Machine type, e.g. db-custom-1-3840 (1 vCPU, 3.75 GB)."
  type        = string

  validation {
    condition     = can(regex("^db-", var.tier))
    error_message = "tier must be a Cloud SQL tier such as db-custom-1-3840."
  }
}

variable "availability_type" {
  description = "ZONAL (one zone) or REGIONAL (standby in a second zone, automatic failover)."
  type        = string

  validation {
    condition     = contains(["ZONAL", "REGIONAL"], var.availability_type)
    error_message = "availability_type must be ZONAL or REGIONAL."
  }
}

variable "disk_size_gb" {
  description = "Starting disk size; it grows on its own."
  type        = number
  default     = 10
}

variable "disk_autoresize_limit_gb" {
  description = "Most the disk may grow to (0 = no limit)."
  type        = number
  default     = 100
}

variable "backups_retained" {
  description = "Daily backups kept."
  type        = number
  default     = 7
}

variable "deletion_protection" {
  description = "Refuse deletion (in Terraform and in GCP) and keep backups after a delete. Off in staging."
  type        = bool
}

variable "databases" {
  description = "Databases to create: riffle for the services, mlflow for the model registry."
  type        = list(string)
  default     = ["riffle", "mlflow"]
}

variable "iam_users" {
  description = "Google identities that may log in, keyed by a short name: service accounts (filled in phase 9) and people, e.g. { migrate = { email = \"...@...iam.gserviceaccount.com\", database_roles = [\"cloudsqlsuperuser\"] }, divyam = { email = \"...@northeastern.edu\", database_roles = [] } }."
  type = map(object({
    email          = string
    database_roles = list(string)
  }))
  default = {}

  validation {
    condition     = alltrue([for u in values(var.iam_users) : can(regex("^[^@\\s]+@[^@\\s]+\\.[a-z]+$", u.email))])
    error_message = "Every iam_users entry needs a full email address."
  }
}

variable "connector_public_ip" {
  description = "Give the instance a public IP that only the Cloud SQL Proxy with a Google login can use (no authorised networks, connector required). Lets the team connect from a laptop; off means pods only."
  type        = bool
  default     = false
}

variable "backup_location" {
  description = "Where backups are stored. A multi-region (us, eu, asia) keeps them safe if the instance's region fails."
  type        = string
  default     = "us"
}

variable "labels" {
  description = "Labels on the instance."
  type        = map(string)
  default     = {}
}
