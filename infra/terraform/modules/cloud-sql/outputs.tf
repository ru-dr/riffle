output "instance_name" {
  description = "Instance name (with its random suffix)."
  value       = google_sql_database_instance.this.name
}

output "connection_name" {
  description = "project:region:instance, what the Cloud SQL connector and Auth Proxy take."
  value       = google_sql_database_instance.this.connection_name
}

output "private_ip" {
  description = "Private address inside the VPC."
  value       = google_sql_database_instance.this.private_ip_address
}

output "databases" {
  description = "Databases created."
  value       = [for d in google_sql_database.this : d.name]
}

output "iam_user_names" {
  description = "Database user name of each IAM login, keyed like iam_users."
  value       = { for k, u in google_sql_user.iam : k => u.name }
}
