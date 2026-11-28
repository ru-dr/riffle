variable "project_id" {
  description = "GCP project."
  type        = string
}

variable "name" {
  description = "Environment name prefix (riffle-staging); also the prefix of the Pub/Sub subscriptions watched."
  type        = string
}

variable "ingress_ip" {
  description = "Public IP of the load balancer (edge module), checked by the uptime check."
  type        = string
}

variable "alert_emails" {
  description = "Addresses alerts are emailed to. Empty: alerts still show in Cloud Monitoring, nobody is emailed."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for e in var.alert_emails : can(regex("^[^@\\s]+@[^@\\s]+\\.[a-z]+$", e))])
    error_message = "Every alert_emails entry must be an email address."
  }
}

variable "max_delivery_attempts" {
  description = "Pub/Sub delivery attempts before dead letter (topics.json), only quoted in the alert text."
  type        = number
  default     = 5
}

variable "backlog_age_seconds" {
  description = "Alert when a subscription's oldest unacknowledged message is older than this."
  type        = number
  default     = 300
}

variable "intake_p99_seconds" {
  description = "Alert when intake's p99 latency is above this (GitHub's limit is 10 s)."
  type        = number
  default     = 2

  validation {
    condition     = var.intake_p99_seconds > 0 && var.intake_p99_seconds < 10
    error_message = "intake_p99_seconds must be between 0 and GitHub's 10 s."
  }
}

variable "scorer_failed_share" {
  description = "Alert when more than this share of PRs the scorer should rank end unranked by a failure (scoring_failed, invalid_input)."
  type        = number
  default     = 0.05

  validation {
    condition     = var.scorer_failed_share > 0 && var.scorer_failed_share < 1
    error_message = "scorer_failed_share must be between 0 and 1."
  }
}

variable "scorer_p95_seconds" {
  description = "Alert when the scorer's p95 time per ranked PR is above this."
  type        = number
  default     = 5
}
