output "uptime_check_id" {
  description = "Id of the intake uptime check."
  value       = google_monitoring_uptime_check_config.intake.uptime_check_id
}

output "alert_policies" {
  description = "Alert policy names."
  value = [
    google_monitoring_alert_policy.uptime.display_name,
    google_monitoring_alert_policy.dead_letter.display_name,
    google_monitoring_alert_policy.backlog_age.display_name,
    google_monitoring_alert_policy.intake_latency.display_name,
  ]
}
