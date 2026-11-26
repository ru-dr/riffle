output "services" {
  description = "The APIs turned on. Other modules depend on this output so they wait for the APIs."
  value       = [for s in google_project_service.this : s.service]
}
