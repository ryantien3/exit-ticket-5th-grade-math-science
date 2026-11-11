# The bucket that holds every layer's state, this one's included (prefix "bootstrap").
resource "google_storage_bucket" "tfstate" {
  name          = "${var.project_id}-tfstate"
  location      = upper(var.region)
  storage_class = "STANDARD"

  # IAM only, no per-object ACLs, and it can never be made public.
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  # Every apply writes a new version of the state file; old ones allow a rollback.
  versioning {
    enabled = true
  }

  # Delete an old version once this many newer versions exist.
  lifecycle_rule {
    condition {
      with_state         = "ARCHIVED"
      num_newer_versions = var.noncurrent_versions_to_keep
    }
    action {
      type = "Delete"
    }
  }

  # Three locks against deleting the bucket that holds every layer's state:
  # the API refuses while objects exist (force_destroy = false); the provider refuses
  # while PREVENT is recorded in state, even if this block is deleted from the code;
  # Terraform refuses to plan a destroy while prevent_destroy is in the code.
  force_destroy   = false
  deletion_policy = "PREVENT"

  lifecycle {
    prevent_destroy = true
  }
}
