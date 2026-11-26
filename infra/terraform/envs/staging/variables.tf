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
