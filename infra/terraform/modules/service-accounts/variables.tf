variable "project_id" {
  description = "GCP project."
  type        = string
}

variable "name_prefix" {
  description = "Prefix of every account id: <name_prefix>-<service>."
  type        = string
}

variable "workload_pool" {
  description = "The cluster's Workload Identity pool (<project>.svc.id.goog), from the cluster module."
  type        = string
}

variable "services" {
  description = "Services keyed by name. Each gets a Google service account and only the permissions listed. The Kubernetes service account is <namespace>/<ksa>, with ksa defaulting to the key."
  type = map(object({
    namespace          = string
    description        = string
    ksa                = optional(string)
    publishes          = optional(list(string), []) # topics.json topic names
    subscribes         = optional(list(string), []) # topics.json subscription names
    buckets            = optional(map(string), {})  # bucket key => role, e.g. { models = "roles/storage.objectViewer" }
    secrets            = optional(list(string), []) # secret keys from the secrets module
    tenant_kek         = optional(bool, false)      # wrap and unwrap tenant data keys
    database           = optional(bool, false)      # log in to Postgres as riffle_app (bound by row-level security)
    database_superuser = optional(bool, false)      # log in as cloudsqlsuperuser: the migration Job only
    project_roles      = optional(list(string), [])
  }))

  validation {
    condition     = alltrue([for k in keys(var.services) : can(regex("^[a-z][a-z0-9-]*[a-z0-9]$", k)) && length("${var.name_prefix}-${k}") <= 30])
    error_message = "Service keys must be lowercase letters, digits and hyphens, and <name_prefix>-<key> at most 30 characters (GCP's limit for an account id)."
  }

  validation {
    condition     = length([for k, s in var.services : k if s.database_superuser]) <= 1
    error_message = "At most one service may be a database superuser (the migration Job): cloudsqlsuperuser ignores row-level security."
  }

  validation {
    condition = alltrue(flatten([for s in values(var.services) : [for role in values(s.buckets) :
    contains(["roles/storage.objectViewer", "roles/storage.objectCreator", "roles/storage.objectUser"], role)]]))
    error_message = "Bucket roles must be roles/storage.objectViewer (read), objectCreator (add new objects only) or objectUser (read, write, delete)."
  }
}

# Lookups from the other modules: short name => name or id in GCP.
variable "topics" {
  description = "Topic name per topics.json name (pubsub module output)."
  type        = map(string)
}

variable "subscriptions" {
  description = "Subscription name per topics.json name (pubsub module output)."
  type        = map(string)
}

variable "buckets" {
  description = "Bucket name per key (storage-buckets module output)."
  type        = map(string)
}

variable "secrets" {
  description = "Secret resource id per key (secrets module output)."
  type        = map(string)
}

variable "tenant_kek_id" {
  description = "Tenant key-encryption key id (kms module output)."
  type        = string
}
