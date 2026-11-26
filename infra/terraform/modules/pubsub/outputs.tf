output "topics" {
  description = "Topic name in GCP per topics.json name, e.g. { pr-events = \"riffle-staging-pr-events\" }."
  value       = { for k, t in google_pubsub_topic.main : k => t.name }
}

output "subscriptions" {
  description = "Subscription name in GCP per topics.json name."
  value       = { for k, s in google_pubsub_subscription.main : k => s.name }
}

output "dead_letter_subscriptions" {
  description = "Where failed messages wait, per topic: read these when something goes wrong."
  value       = { for k, s in google_pubsub_subscription.hold : k => s.name }
}
