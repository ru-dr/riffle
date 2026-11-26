# Turns on the Google APIs the platform uses. Every other module depends on this one, so a
# missing API shows up here, by name, instead of as an error inside some later resource.

resource "google_project_service" "this" {
  for_each = toset(var.services)

  project = var.project_id
  service = each.value

  # Turning an API off on destroy would break anything else in the project that uses it.
  disable_on_destroy         = false
  disable_dependent_services = false
}
