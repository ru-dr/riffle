variable "project_id" {
  description = "GCP project."
  type        = string
}

variable "region" {
  description = "Region; the cluster is regional (spread over the region's zones)."
  type        = string
}

variable "name" {
  description = "Cluster name, also the prefix of the node service account (max 24 characters)."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,23}$", var.name))
    error_message = "name must be 5-24 lowercase letters, digits or hyphens, starting with a letter (the node service account id adds '-nodes' and is limited to 30)."
  }
}

variable "network_id" {
  description = "VPC id, from the network module."
  type        = string
}

variable "subnet_id" {
  description = "Subnet id, from the network module."
  type        = string
}

variable "pods_range_name" {
  description = "Secondary range for pods, from the network module."
  type        = string
}

variable "services_range_name" {
  description = "Secondary range for services, from the network module."
  type        = string
}

variable "release_channel" {
  description = "GKE release channel: how soon the cluster gets new Kubernetes versions."
  type        = string
  default     = "REGULAR"

  validation {
    condition     = contains(["RAPID", "REGULAR", "STABLE"], var.release_channel)
    error_message = "release_channel must be RAPID, REGULAR or STABLE."
  }
}

variable "deletion_protection" {
  description = "Refuse terraform destroy on the cluster. Off in staging, on in prod."
  type        = bool
}
