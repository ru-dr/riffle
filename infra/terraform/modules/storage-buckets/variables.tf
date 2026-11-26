variable "project_id" {
  description = "GCP project."
  type        = string
}

variable "location" {
  description = "Region (or multi-region) of every bucket."
  type        = string
}

variable "name_prefix" {
  description = "Bucket names are <name_prefix>-<key>. Bucket names are global across all of GCP, so include the project id."
  type        = string
}

variable "buckets" {
  description = "Buckets to create, keyed by short name (e.g. score-log)."
  type = map(object({
    purpose                  = string # one line: what is stored there
    versioning               = optional(bool, false)
    nearline_after_days      = optional(number) # move to Nearline after this many days
    delete_after_days        = optional(number) # delete objects after this many days
    noncurrent_versions_kept = optional(number) # with versioning: old versions kept per object
  }))

  validation {
    condition     = alltrue([for k in keys(var.buckets) : can(regex("^[a-z0-9][a-z0-9-]*[a-z0-9]$", k))])
    error_message = "Bucket keys must be lowercase letters, digits and hyphens."
  }

  validation {
    condition = alltrue([for b in values(var.buckets) :
    b.nearline_after_days == null || b.delete_after_days == null || try(b.nearline_after_days < b.delete_after_days, true)])
    error_message = "nearline_after_days must be smaller than delete_after_days."
  }

  validation {
    condition     = alltrue([for b in values(var.buckets) : b.noncurrent_versions_kept == null || b.versioning])
    error_message = "noncurrent_versions_kept needs versioning = true."
  }
}

variable "soft_delete_days" {
  description = "Days a deleted or overwritten object can be restored (0 turns it off, 7 is GCP's default, at most 90)."
  type        = number
  default     = 7

  validation {
    condition     = var.soft_delete_days == 0 || (var.soft_delete_days >= 7 && var.soft_delete_days <= 90)
    error_message = "soft_delete_days must be 0 or between 7 and 90."
  }
}

variable "force_destroy" {
  description = "Let terraform destroy delete buckets that still hold objects. On in staging only."
  type        = bool
}

variable "labels" {
  description = "Labels on every bucket (a bucket label is added)."
  type        = map(string)
  default     = {}
}
