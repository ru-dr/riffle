output "grants" {
  description = "Every member and role granted, as \"<member> <role>\"."
  value       = sort(keys(local.grants))
}
