variable "project_id" {
  description = "GCP project."
  type        = string
}

variable "region" {
  description = "The one region every secret is stored in."
  type        = string
}

variable "name" {
  description = "Prefix of every secret id: <name>-<key>."
  type        = string
}

variable "secrets" {
  description = "Secrets to create, keyed by short name. set_by is hand (value added later with gcloud) or terraform (value from var.values)."
  type = map(object({
    purpose = string
    set_by  = string
  }))

  validation {
    condition     = alltrue([for s in values(var.secrets) : contains(["hand", "terraform"], s.set_by)])
    error_message = "set_by must be hand or terraform."
  }

  validation {
    condition     = alltrue([for k in keys(var.secrets) : can(regex("^[a-z0-9-]+$", k))])
    error_message = "Secret keys must be lowercase letters, digits and hyphens."
  }
}

variable "values" {
  description = "Value of every secret with set_by = terraform, keyed like var.secrets."
  type        = map(string)
  sensitive   = true
  default     = {}
}

variable "labels" {
  description = "Labels on every secret (a source label is added)."
  type        = map(string)
  default     = {}
}
