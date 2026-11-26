variable "project_id" {
  description = "GCP project."
  type        = string
}

variable "region" {
  description = "Region of the subnet, router and NAT."
  type        = string
}

variable "name" {
  description = "Prefix for every resource name, e.g. riffle-staging."
  type        = string
}

variable "subnet_cidr" {
  description = "Node addresses."
  type        = string
}

variable "pods_cidr" {
  description = "Secondary range GKE gives pods."
  type        = string
}

variable "services_cidr" {
  description = "Secondary range GKE gives Kubernetes services."
  type        = string
}

variable "private_services_cidr" {
  description = "Range reserved for Cloud SQL and Memorystore private addresses (a /16 to /24)."
  type        = string

  validation {
    condition     = can(cidrhost(var.private_services_cidr, 0)) && tonumber(split("/", var.private_services_cidr)[1]) <= 24
    error_message = "private_services_cidr must be a CIDR of size /24 or larger."
  }
}
