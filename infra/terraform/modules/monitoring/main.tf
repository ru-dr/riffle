# Uptime check and alerts (diagram: "Uptime checks", "Alertmanager · SLO burn · queue · drift",
# "On-call"). On GKE the alerting is Cloud Monitoring's: it evaluates PromQL over Managed
# Prometheus (riffle_* metrics) and over Google's own metrics (Pub/Sub), and notifies the
# channels below. No Alertmanager to run.

resource "google_monitoring_notification_channel" "email" {
  for_each = toset(var.alert_emails)

  project      = var.project_id
  display_name = "Riffle ${var.name}: ${each.value}"
  type         = "email"
  labels = {
    email_address = each.value
  }
}

locals {
  channels = [for c in google_monitoring_notification_channel.email : c.id]
}

# Calls intake's /healthz through the load balancer from several places in the world, every
# minute: the same path GitHub's webhooks take.
resource "google_monitoring_uptime_check_config" "intake" {
  project      = var.project_id
  display_name = "${var.name} intake /healthz"
  period       = "60s"
  timeout      = "10s"

  http_check {
    path    = "/healthz"
    port    = 80
    use_ssl = false # no domain yet, so no certificate
  }

  monitored_resource {
    type = "uptime_url"
    labels = {
      project_id = var.project_id
      host       = var.ingress_ip
    }
  }
}

resource "google_monitoring_alert_policy" "uptime" {
  project               = var.project_id
  display_name          = "${var.name}: intake unreachable"
  combiner              = "OR"
  severity              = "CRITICAL"
  notification_channels = local.channels

  conditions {
    display_name = "Uptime check failing"
    condition_threshold {
      filter          = "metric.type=\"monitoring.googleapis.com/uptime_check/check_passed\" AND metric.label.check_id=\"${google_monitoring_uptime_check_config.intake.uptime_check_id}\" AND resource.type=\"uptime_url\""
      comparison      = "COMPARISON_GT"
      threshold_value = 1
      duration        = "120s"
      aggregations {
        alignment_period     = "1200s"
        per_series_aligner   = "ALIGN_NEXT_OLDER"
        cross_series_reducer = "REDUCE_COUNT_FALSE"
        group_by_fields      = ["resource.label.*"]
      }
    }
  }

  documentation {
    subject   = "Riffle ${var.name}: intake is unreachable from outside"
    content   = "GitHub's webhooks cannot reach intake, so PRs are not being ranked. Check the Gateway, intake's pods and Cloud Armor. infra/k8s/README.md, Troubleshooting."
    mime_type = "text/markdown"
  }

  alert_strategy {
    auto_close = "1800s"
  }
}

# A message reached a dead-letter topic: a consumer failed it max_delivery_attempts times.
resource "google_monitoring_alert_policy" "dead_letter" {
  project               = var.project_id
  display_name          = "${var.name}: messages in dead letter"
  combiner              = "OR"
  severity              = "ERROR"
  notification_channels = local.channels

  conditions {
    display_name = "Dead-letter hold subscription not empty"
    condition_prometheus_query_language {
      query               = "sum by (subscription_id) (pubsub_googleapis_com:subscription_num_undelivered_messages{monitored_resource=\"pubsub_subscription\", subscription_id=~\"${var.name}-.*-dead-letter-hold\"}) > 0"
      duration            = "300s"
      evaluation_interval = "60s"
    }
  }

  documentation {
    subject   = "Riffle ${var.name}: messages in dead letter"
    content   = "A consumer failed a message ${var.max_delivery_attempts} times. Read the message from the subscription named in the alert, fix the consumer, then replay it."
    mime_type = "text/markdown"
  }

  alert_strategy {
    auto_close = "3600s"
  }
}

# A consumer has stopped keeping up: the oldest unacknowledged message is getting old.
resource "google_monitoring_alert_policy" "backlog_age" {
  project               = var.project_id
  display_name          = "${var.name}: queue falling behind"
  combiner              = "OR"
  severity              = "WARNING"
  notification_channels = local.channels

  conditions {
    display_name = "Oldest unacknowledged message older than ${var.backlog_age_seconds} s"
    condition_prometheus_query_language {
      query               = "max by (subscription_id) (pubsub_googleapis_com:subscription_oldest_unacked_message_age{monitored_resource=\"pubsub_subscription\", subscription_id=~\"${var.name}-.*\", subscription_id!~\".*-dead-letter-hold\"}) > ${var.backlog_age_seconds}"
      duration            = "300s"
      evaluation_interval = "60s"
    }
  }

  documentation {
    subject   = "Riffle ${var.name}: a queue is falling behind"
    content   = "The service reading the subscription named in the alert is slow, down or not scaling. Check its pods and KEDA."
    mime_type = "text/markdown"
  }

  alert_strategy {
    auto_close = "1800s"
  }
}

# GitHub allows 10 s for a webhook answer; intake must stay far below.
resource "google_monitoring_alert_policy" "intake_latency" {
  project               = var.project_id
  display_name          = "${var.name}: intake slow"
  combiner              = "OR"
  severity              = "WARNING"
  notification_channels = local.channels

  conditions {
    display_name = "intake p99 latency above ${var.intake_p99_seconds} s"
    condition_prometheus_query_language {
      query               = "histogram_quantile(0.99, sum by (le) (rate(riffle_intake_latency_seconds_bucket{namespace=\"riffle-serving\"}[5m]))) > ${var.intake_p99_seconds}"
      duration            = "600s"
      evaluation_interval = "60s"
      # The metric appears once intake runs; until then the policy waits.
      disable_metric_validation = true
    }
  }

  documentation {
    subject   = "Riffle ${var.name}: intake is slow to answer GitHub"
    content   = "p99 of riffle_intake_latency_seconds is above ${var.intake_p99_seconds} s; GitHub gives up at 10 s. Check Redis and Pub/Sub publish latency."
    mime_type = "text/markdown"
  }

  alert_strategy {
    auto_close = "1800s"
  }
}
