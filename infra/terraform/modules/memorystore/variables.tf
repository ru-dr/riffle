variable "project_id" {
  description = "GCP project."
  type        = string
}

variable "region" {
  description = "Region of the instance."
  type        = string
}

variable "name" {
  description = "Instance name."
  type        = string
}

variable "network_id" {
  description = "VPC id. Private services access must already exist on it (network module)."
  type        = string
}

variable "tier" {
  description = "BASIC (one node) or STANDARD_HA (replica in a second zone, automatic failover)."
  type        = string

  validation {
    condition     = contains(["BASIC", "STANDARD_HA"], var.tier)
    error_message = "tier must be BASIC or STANDARD_HA."
  }
}

variable "memory_size_gb" {
  description = "Memory in GB (1 is the smallest)."
  type        = number
  default     = 1

  validation {
    condition     = var.memory_size_gb >= 1 && var.memory_size_gb <= 300
    error_message = "memory_size_gb must be between 1 and 300."
  }
}

variable "redis_version" {
  description = "Redis version, as Memorystore names it."
  type        = string
  default     = "REDIS_7_2"

  validation {
    condition     = can(regex("^REDIS_[0-9]+_[0-9]+$", var.redis_version))
    error_message = "redis_version must look like REDIS_7_2."
  }
}

variable "deletion_protection" {
  description = "Refuse deleting the instance. Off in staging."
  type        = bool
}

variable "labels" {
  description = "Labels on the instance."
  type        = map(string)
  default     = {}
}
