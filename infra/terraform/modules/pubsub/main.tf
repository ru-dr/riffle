# The message queue between the services (diagram: "Message queue"), built from
# infra/pubsub/topics.json. Per topic: the topic, its subscriptions, a dead-letter topic and a
# hold subscription that keeps dead letters. Who may publish or read is set per service in
# phase 9.

locals {
  topics = { for t in var.spec.topics : t.name => t }

  subscriptions = merge([
    for t in var.spec.topics : {
      for s in t.subscriptions : s.name => merge(s, { topic = t.name })
    }
  ]...)

  # Pub/Sub's own service agent moves a message to the dead-letter topic after the last try.
  pubsub_agent = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}

data "google_project" "this" {
  project_id = var.project_id
}

resource "google_pubsub_topic" "main" {
  for_each = local.topics

  project = var.project_id
  name    = "${var.name_prefix}-${each.key}"

  # Messages are stored only in this region: nothing leaves the installation's region.
  message_storage_policy {
    allowed_persistence_regions = [var.region]
  }

  labels = var.labels
}

resource "google_pubsub_topic" "dead_letter" {
  for_each = local.topics

  project = var.project_id
  name    = "${var.name_prefix}-${each.key}-dead-letter"

  message_storage_policy {
    allowed_persistence_regions = [var.region]
  }

  labels = merge(var.labels, { dead_letter = "true" })
}

resource "google_pubsub_subscription" "main" {
  for_each = local.subscriptions

  project = var.project_id
  name    = "${var.name_prefix}-${each.key}"
  topic   = google_pubsub_topic.main[each.value.topic].id

  ack_deadline_seconds       = each.value.ack_deadline_seconds
  enable_message_ordering    = each.value.message_ordering
  message_retention_duration = "${var.message_retention_days * 86400}s" # unacked messages kept this long

  retry_policy {
    minimum_backoff = "${each.value.retry_backoff_seconds[0]}s"
    maximum_backoff = "${each.value.retry_backoff_seconds[1]}s"
  }

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.dead_letter[each.value.topic].id
    max_delivery_attempts = var.spec.dead_letter.max_delivery_attempts
  }

  # Never expire. By default Pub/Sub deletes a subscription nobody has read for 31 days,
  # which would silently drop a quiet staging environment's queues.
  expiration_policy {
    ttl = ""
  }

  labels = merge(var.labels, { consumer = replace(lower(each.value.consumer), "/[^a-z0-9_-]/", "-") })
}

# Without a subscription on it, a dead-letter topic would discard what it receives.
resource "google_pubsub_subscription" "hold" {
  for_each = local.topics

  project = var.project_id
  name    = "${var.name_prefix}-${each.key}-dead-letter-hold"
  topic   = google_pubsub_topic.dead_letter[each.key].id

  ack_deadline_seconds       = 60
  message_retention_duration = "${var.spec.dead_letter.hold_retention_days * 86400}s"

  expiration_policy {
    ttl = ""
  }

  labels = merge(var.labels, { dead_letter = "true" })
}

# The two permissions dead-lettering needs: publish to the dead-letter topic, and acknowledge
# the failed message on the original subscription.
resource "google_pubsub_topic_iam_member" "dead_letter_publisher" {
  for_each = local.topics

  project = var.project_id
  topic   = google_pubsub_topic.dead_letter[each.key].name
  role    = "roles/pubsub.publisher"
  member  = local.pubsub_agent
}

resource "google_pubsub_subscription_iam_member" "dead_letter_subscriber" {
  for_each = local.subscriptions

  project      = var.project_id
  subscription = google_pubsub_subscription.main[each.key].name
  role         = "roles/pubsub.subscriber"
  member       = local.pubsub_agent
}
