variable "project_id" {
  description = "GCP project."
  type        = string
}

variable "region" {
  description = "Location of the key ring (keys are used from the same region)."
  type        = string
}

variable "name" {
  description = "Key ring name prefix; a random suffix is added."
  type        = string
}

variable "rotation_days" {
  description = "Days between automatic key versions."
  type        = number
  default     = 90

  validation {
    condition     = var.rotation_days >= 1 && var.rotation_days <= 365
    error_message = "rotation_days must be between 1 and 365."
  }
}

variable "destroy_scheduled_days" {
  description = "Days a destroyed key version can still be restored (GCP allows 1 to 120)."
  type        = number
  default     = 30

  validation {
    condition     = var.destroy_scheduled_days >= 1 && var.destroy_scheduled_days <= 120
    error_message = "destroy_scheduled_days must be between 1 and 120."
  }
}

variable "protection_level" {
  description = "SOFTWARE, or HSM (hardware-backed, costs more)."
  type        = string
  default     = "SOFTWARE"

  validation {
    condition     = contains(["SOFTWARE", "HSM"], var.protection_level)
    error_message = "protection_level must be SOFTWARE or HSM."
  }
}

variable "labels" {
  description = "Labels on the key."
  type        = map(string)
  default     = {}
}
