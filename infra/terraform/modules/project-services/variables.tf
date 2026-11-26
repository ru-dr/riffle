variable "project_id" {
  description = "GCP project to turn the APIs on in."
  type        = string
}

variable "services" {
  description = "API names, e.g. container.googleapis.com."
  type        = list(string)

  validation {
    condition     = alltrue([for s in var.services : can(regex("^[a-z0-9-]+\\.googleapis\\.com$", s))])
    error_message = "Every service must look like <name>.googleapis.com."
  }
}
