variable "project_id" {
  description = "GCP project."
  type        = string
}

variable "region" {
  description = "Region of the Artifact Registry repository."
  type        = string
}

variable "name" {
  description = "Prefix of the pool and the two account ids (<name>-ci-terraform must fit in 30 characters)."
  type        = string

  validation {
    condition     = length("${var.name}-ci-terraform") <= 30 && length("${var.name}-gh-0000") <= 32
    error_message = "name is too long: <name>-ci-terraform must be at most 30 characters."
  }
}

variable "repository" {
  description = "The only GitHub repository whose workflow runs are trusted, as owner/name."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$", var.repository))
    error_message = "repository must be owner/name."
  }
}

variable "terraform_ref" {
  description = "The only git ref whose runs may act as ci-terraform, e.g. refs/heads/main."
  type        = string
  default     = "refs/heads/main"

  validation {
    condition     = startswith(var.terraform_ref, "refs/")
    error_message = "terraform_ref must be a full ref such as refs/heads/main."
  }
}

variable "artifact_repository" {
  description = "Artifact Registry repository ci-images may push to (artifact-registry module)."
  type        = string
}

variable "terraform_roles" {
  description = "Project roles of ci-terraform: what Terraform needs to create and change everything in envs/."
  type        = list(string)
}
