# Cloud Storage buckets (diagram: "Object storage", "Score log"). Every bucket is private, with
# the same safety settings; what differs per bucket (versioning, when data gets cheaper or is
# deleted) comes from var.buckets.

resource "google_storage_bucket" "this" {
  for_each = var.buckets

  project  = var.project_id
  name     = "${var.name_prefix}-${each.key}"
  location = var.location

  storage_class               = "STANDARD"
  uniform_bucket_level_access = true       # permissions per bucket only, no per-object ACLs
  public_access_prevention    = "enforced" # can never be made public, even by mistake
  force_destroy               = var.force_destroy

  # A deleted or overwritten object can be restored for this long.
  soft_delete_policy {
    retention_duration_seconds = var.soft_delete_days * 86400
  }

  versioning {
    enabled = each.value.versioning
  }

  # Older data moves to Nearline, which costs less to keep and more to read.
  dynamic "lifecycle_rule" {
    for_each = each.value.nearline_after_days == null ? [] : [each.value.nearline_after_days]
    content {
      condition {
        age                   = lifecycle_rule.value
        matches_storage_class = ["STANDARD"]
      }
      action {
        type          = "SetStorageClass"
        storage_class = "NEARLINE"
      }
    }
  }

  # Data that is only useful for a while is deleted.
  dynamic "lifecycle_rule" {
    for_each = each.value.delete_after_days == null ? [] : [each.value.delete_after_days]
    content {
      condition {
        age = lifecycle_rule.value
      }
      action {
        type = "Delete"
      }
    }
  }

  # With versioning, keep only the last few old versions of an object, and none older than 30 days.
  dynamic "lifecycle_rule" {
    for_each = each.value.versioning && each.value.noncurrent_versions_kept != null ? [each.value.noncurrent_versions_kept] : []
    content {
      condition {
        num_newer_versions = lifecycle_rule.value
        with_state         = "ARCHIVED"
      }
      action {
        type = "Delete"
      }
    }
  }
  dynamic "lifecycle_rule" {
    for_each = each.value.versioning ? [30] : []
    content {
      condition {
        days_since_noncurrent_time = lifecycle_rule.value
      }
      action {
        type = "Delete"
      }
    }
  }

  labels = merge(var.labels, { bucket = each.key })
}
