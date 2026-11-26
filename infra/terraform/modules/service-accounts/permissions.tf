# Every permission a service gets, one resource per kind. Each grant is on one topic,
# subscription, bucket, secret or key, never on the whole project, except the two Cloud SQL
# roles (Cloud SQL has no per-instance permissions) and any project_roles listed explicitly.
#
# Resource keys are "<service>/<thing>", so plan output and errors name both.

locals {
  member = { for k, sa in google_service_account.this : k => "serviceAccount:${sa.email}" }

  publish = merge([for s, c in var.services : { for t in c.publishes : "${s}/${t}" => { service = s, topic = t } }]...)

  subscribe = merge([for s, c in var.services : { for sub in c.subscribes : "${s}/${sub}" => { service = s, subscription = sub } }]...)

  bucket = merge([for s, c in var.services : { for b, role in c.buckets : "${s}/${b}" => { service = s, bucket = b, role = role } }]...)

  secret = merge([for s, c in var.services : { for sec in c.secrets : "${s}/${sec}" => { service = s, secret = sec } }]...)

  kms = { for s, c in var.services : s => c if c.tenant_kek }

  # Logging in to Cloud SQL as an IAM user needs both: connect through the connector (client)
  # and log in with a Google identity (instanceUser).
  database = merge([for s, c in var.services : {
    for role in ["roles/cloudsql.client", "roles/cloudsql.instanceUser"] : "${s}/${role}" => { service = s, role = role }
  } if c.database || c.database_superuser]...)

  project_role = merge([for s, c in var.services : { for role in c.project_roles : "${s}/${role}" => { service = s, role = role } }]...)
}

# Publish to a topic.
resource "google_pubsub_topic_iam_member" "publish" {
  for_each = local.publish

  project = var.project_id
  topic   = var.topics[each.value.topic]
  role    = "roles/pubsub.publisher"
  member  = local.member[each.value.service]
}

# Read and acknowledge on a subscription.
resource "google_pubsub_subscription_iam_member" "subscribe" {
  for_each = local.subscribe

  project      = var.project_id
  subscription = var.subscriptions[each.value.subscription]
  role         = "roles/pubsub.subscriber"
  member       = local.member[each.value.service]
}

# Read or write objects in one bucket.
resource "google_storage_bucket_iam_member" "bucket" {
  for_each = local.bucket

  bucket = var.buckets[each.value.bucket]
  role   = each.value.role
  member = local.member[each.value.service]
}

# Read one secret. External Secrets reads it as this service, so only this service's
# namespace can get it.
resource "google_secret_manager_secret_iam_member" "secret" {
  for_each = local.secret

  project   = var.project_id
  secret_id = var.secrets[each.value.secret]
  role      = "roles/secretmanager.secretAccessor"
  member    = local.member[each.value.service]
}

# Wrap and unwrap tenant data keys with the tenant key-encryption key (ADR 0025).
resource "google_kms_crypto_key_iam_member" "tenant_kek" {
  for_each = local.kms

  crypto_key_id = var.tenant_kek_id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = local.member[each.key]
}

# Connect to Cloud SQL and log in as an IAM user.
resource "google_project_iam_member" "database" {
  for_each = local.database

  project = var.project_id
  role    = each.value.role
  member  = local.member[each.value.service]
}

# Anything else that only exists project-wide (e.g. reading metrics for KEDA).
resource "google_project_iam_member" "project_role" {
  for_each = local.project_role

  project = var.project_id
  role    = each.value.role
  member  = local.member[each.value.service]
}
