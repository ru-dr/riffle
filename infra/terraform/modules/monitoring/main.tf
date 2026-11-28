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

# intake answered GitHub 503 because Pub/Sub did not take the message. GitHub does not redeliver
# by itself, so those PRs are missing until someone redelivers them from the App's settings.
resource "google_monitoring_alert_policy" "intake_publish_failed" {
  project               = var.project_id
  display_name          = "${var.name}: intake cannot publish"
  combiner              = "OR"
  severity              = "ERROR"
  notification_channels = local.channels

  conditions {
    display_name = "Webhooks answered 503 (publish_failed)"
    condition_prometheus_query_language {
      query                     = "sum(rate(riffle_intake_events_total{namespace=\"riffle-serving\", result=\"publish_failed\"}[10m])) > 0"
      duration                  = "300s"
      evaluation_interval       = "60s"
      disable_metric_validation = true
    }
  }

  documentation {
    subject   = "Riffle ${var.name}: intake cannot publish webhooks"
    content   = "intake answered GitHub 503 because Pub/Sub did not store the message. GitHub does not retry by itself: once Pub/Sub is back, redeliver the failed deliveries from the GitHub App's settings (Advanced, Recent deliveries)."
    mime_type = "text/markdown"
  }

  alert_strategy {
    auto_close = "1800s"
  }
}

# ---- scorer (services/scorer). Its metrics exist once it runs; until then these policies wait.
# Every PR is stored and shown either way (fail open); these say when PRs stop being ranked.

locals {
  scorer = "namespace=\"riffle-serving\""
}

# PRs arrive and no model is loaded: none uploaded to MLflow yet, or MLflow never answered.
resource "google_monitoring_alert_policy" "scorer_no_model" {
  project               = var.project_id
  display_name          = "${var.name}: scorer has no model"
  combiner              = "OR"
  severity              = "ERROR"
  notification_channels = local.channels

  conditions {
    display_name = "PRs stored unranked, model_unavailable"
    condition_prometheus_query_language {
      query                     = "sum(rate(riffle_scorer_messages_total{${local.scorer}, result=\"model_unavailable\"}[10m])) > 0"
      duration                  = "600s"
      evaluation_interval       = "60s"
      disable_metric_validation = true
    }
  }

  documentation {
    subject   = "Riffle ${var.name}: PRs are not ranked, the scorer has no model"
    content   = "The scorer found no model at riffle-outcome-setup-g@champion. Check that a version is registered and has the alias champion (services/scorer/README.md, Uploading the model), and that MLflow is up in riffle-ml."
    mime_type = "text/markdown"
  }

  alert_strategy {
    auto_close = "1800s"
  }
}

# PRs that should have been ranked end unranked: scoring failed three times, or the row was invalid.
resource "google_monitoring_alert_policy" "scorer_failing" {
  project               = var.project_id
  display_name          = "${var.name}: scorer failing PRs"
  combiner              = "OR"
  severity              = "ERROR"
  notification_channels = local.channels

  conditions {
    display_name = "More than ${var.scorer_failed_share * 100}% of PRs unranked by a failure"
    condition_prometheus_query_language {
      query                     = "sum(rate(riffle_scorer_messages_total{${local.scorer}, result=~\"scoring_failed|invalid_input\"}[15m])) / sum(rate(riffle_scorer_messages_total{${local.scorer}, result=~\"ranked|scoring_failed|invalid_input\"}[15m])) > ${var.scorer_failed_share}"
      duration                  = "900s"
      evaluation_interval       = "60s"
      disable_metric_validation = true
    }
  }

  documentation {
    subject   = "Riffle ${var.name}: the scorer is leaving PRs unranked"
    content   = "PRs are stored unranked with scoring_failed (scoring raised three times: often the history store) or invalid_input (the row breaks its contract, or comes from a miner version the model was not trained on). The scorer's logs name the reason: Grafana, Riffle live path, or Loki {service=\"scorer\"}."
    mime_type = "text/markdown"
  }

  alert_strategy {
    auto_close = "1800s"
  }
}

# The scorer cannot store or publish: Postgres or Pub/Sub. Messages wait and are redelivered.
resource "google_monitoring_alert_policy" "scorer_errors" {
  project               = var.project_id
  display_name          = "${var.name}: scorer cannot store or publish"
  combiner              = "OR"
  severity              = "ERROR"
  notification_channels = local.channels

  conditions {
    display_name = "Messages redelivered because the store or Pub/Sub failed"
    condition_prometheus_query_language {
      query                     = "sum(rate(riffle_scorer_messages_total{${local.scorer}, result=\"error\"}[10m])) > 0"
      duration                  = "600s"
      evaluation_interval       = "60s"
      disable_metric_validation = true
    }
  }

  documentation {
    subject   = "Riffle ${var.name}: the scorer cannot store or publish scores"
    content   = "Scores are not reaching Postgres or the score-results topic, so PRs wait. Check the cloud-sql-proxy sidecar and the scorer's logs. After ${var.max_delivery_attempts} deliveries a message goes to the dead letter and is kept 7 days."
    mime_type = "text/markdown"
  }

  alert_strategy {
    auto_close = "1800s"
  }
}

# Score log rows dropped (ADR 0024): the score still posted, but monitoring and retraining lose the row.
resource "google_monitoring_alert_policy" "score_log_dropped" {
  project               = var.project_id
  display_name          = "${var.name}: score log rows dropped"
  combiner              = "OR"
  severity              = "WARNING"
  notification_channels = local.channels

  conditions {
    display_name = "riffle_score_log_dropped_total increased"
    condition_prometheus_query_language {
      query                     = "sum(increase(riffle_score_log_dropped_total{${local.scorer}}[15m])) > 0"
      duration                  = "0s"
      evaluation_interval       = "60s"
      disable_metric_validation = true
    }
  }

  documentation {
    subject   = "Riffle ${var.name}: score log rows were dropped"
    content   = "The scorer could not publish to score-log. Scores were not affected; the dropped rows are missing from the score log used for monitoring and retraining. Check Pub/Sub and the scorer's logs (\"score log not published\")."
    mime_type = "text/markdown"
  }

  alert_strategy {
    auto_close = "3600s"
  }
}

# Scoring is slow (history download, model, Postgres).
resource "google_monitoring_alert_policy" "scorer_latency" {
  project               = var.project_id
  display_name          = "${var.name}: scorer slow"
  combiner              = "OR"
  severity              = "WARNING"
  notification_channels = local.channels

  conditions {
    display_name = "scorer p95 above ${var.scorer_p95_seconds} s"
    condition_prometheus_query_language {
      query                     = "histogram_quantile(0.95, sum by (le) (rate(riffle_scorer_latency_seconds_bucket{${local.scorer}, result=\"ranked\"}[10m]))) > ${var.scorer_p95_seconds}"
      duration                  = "900s"
      evaluation_interval       = "60s"
      disable_metric_validation = true
    }
  }

  documentation {
    subject   = "Riffle ${var.name}: the scorer is slow"
    content   = "p95 of riffle_scorer_latency_seconds for ranked PRs is above ${var.scorer_p95_seconds} s. Usual causes: history files downloaded often (HISTORY_CACHE_SECONDS), Postgres slow, or too few pods (KEDA)."
    mime_type = "text/markdown"
  }

  alert_strategy {
    auto_close = "1800s"
  }
}

# The score-log writer cannot write files: Postgres (the tenant's key), KMS or the bucket. Rows wait
# in the queue; after max_delivery_attempts they go to the dead letter.
resource "google_monitoring_alert_policy" "score_log_writer_failing" {
  project               = var.project_id
  display_name          = "${var.name}: score log files not written"
  combiner              = "OR"
  severity              = "WARNING"
  notification_channels = local.channels

  conditions {
    display_name = "riffle_score_log_write_failures_total increasing"
    condition_prometheus_query_language {
      query                     = "sum(rate(riffle_score_log_write_failures_total{namespace=\"riffle-workers\"}[15m])) > 0"
      duration                  = "900s"
      evaluation_interval       = "60s"
      disable_metric_validation = true
    }
  }

  documentation {
    subject   = "Riffle ${var.name}: the score-log writer cannot write files"
    content   = "Score log rows are waiting in the queue. The writer's logs (\"score log file not written\") say which step failed: the tenant's data key (Postgres, KMS tenant-kek) or the score-log bucket. Scores are not affected."
    mime_type = "text/markdown"
  }

  alert_strategy {
    auto_close = "1800s"
  }
}
