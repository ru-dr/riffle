variable "project_id" {
  description = "GCP project."
  type        = string
}

variable "region" {
  description = "The one region messages are stored in."
  type        = string
}

variable "name_prefix" {
  description = "Prefix of every topic and subscription name, e.g. riffle-staging."
  type        = string
}

variable "spec" {
  description = "The decoded infra/pubsub/topics.json."
  type = object({
    dead_letter = object({
      max_delivery_attempts = number
      hold_retention_days   = number
    })
    topics = list(object({
      name  = string
      about = string
      subscriptions = list(object({
        name                  = string
        consumer              = string
        ack_deadline_seconds  = number
        message_ordering      = bool
        retry_backoff_seconds = list(number)
      }))
    }))
  })

  validation {
    condition     = var.spec.dead_letter.max_delivery_attempts >= 5 && var.spec.dead_letter.max_delivery_attempts <= 100
    error_message = "topics.json: dead_letter.max_delivery_attempts must be 5-100 (Pub/Sub's limits)."
  }

  validation {
    condition     = var.spec.dead_letter.hold_retention_days >= 1 && var.spec.dead_letter.hold_retention_days <= 31
    error_message = "topics.json: dead_letter.hold_retention_days must be 1-31 (Pub/Sub keeps messages at most 31 days)."
  }

  validation {
    condition     = alltrue([for t in var.spec.topics : length(t.subscriptions) > 0])
    error_message = "topics.json: every topic needs at least one subscription, or its messages are never read."
  }

  validation {
    condition = alltrue(flatten([for t in var.spec.topics : concat(
      [can(regex("^[a-z][a-z0-9-]{2,100}$", t.name))],
      [for s in t.subscriptions : can(regex("^[a-z][a-z0-9-]{2,100}$", s.name))]
    )]))
    error_message = "topics.json: topic and subscription names must be lowercase letters, digits and hyphens, starting with a letter."
  }

  validation {
    condition = alltrue(flatten([for t in var.spec.topics : [for s in t.subscriptions :
    s.ack_deadline_seconds >= 10 && s.ack_deadline_seconds <= 600]]))
    error_message = "topics.json: ack_deadline_seconds must be 10-600."
  }

  validation {
    condition = alltrue(flatten([for t in var.spec.topics : [for s in t.subscriptions :
    length(s.retry_backoff_seconds) == 2 && try(s.retry_backoff_seconds[0] >= 0 && s.retry_backoff_seconds[0] <= s.retry_backoff_seconds[1] && s.retry_backoff_seconds[1] <= 600, false)]]))
    error_message = "topics.json: retry_backoff_seconds must be [min, max] with 0 <= min <= max <= 600."
  }

  validation {
    condition = (
      length(distinct(concat([for t in var.spec.topics : t.name], flatten([for t in var.spec.topics : [for s in t.subscriptions : s.name]])))) ==
      length(var.spec.topics) + length(flatten([for t in var.spec.topics : t.subscriptions]))
    )
    error_message = "topics.json: every topic and subscription name must be unique."
  }
}

variable "message_retention_days" {
  description = "Days an unacknowledged message stays on a subscription (Pub/Sub allows up to 31)."
  type        = number
  default     = 7

  validation {
    condition     = var.message_retention_days >= 1 && var.message_retention_days <= 31
    error_message = "message_retention_days must be 1-31."
  }
}

variable "labels" {
  description = "Labels on every topic and subscription."
  type        = map(string)
  default     = {}
}
