variable "project_id" {
  description = "GCP project."
  type        = string
}

variable "region" {
  description = "Region of the registry (same as the cluster, so pulls stay in the region)."
  type        = string
}

variable "name" {
  description = "Repository id."
  type        = string
}

variable "keep_recent_images" {
  description = "Images kept per service, newest first, whatever else the cleanup says."
  type        = number
  default     = 20
}

variable "untagged_max_age_days" {
  description = "Untagged images older than this are deleted."
  type        = number
  default     = 30
}

variable "labels" {
  description = "Labels on the repository."
  type        = map(string)
  default     = {}
}
