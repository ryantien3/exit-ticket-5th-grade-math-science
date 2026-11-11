# Docker images for the api and web (Phase 3B). Format and location are permanent.
resource "google_artifact_registry_repository" "exit_ticket" {
  location      = var.region
  repository_id = "exit-ticket"
  format        = "DOCKER"
  description   = "Exit Ticket container images (api, web)"
  labels = {
    app = "exit-ticket"
    env = "dev"
  }

  # Deleting the repository deletes every image the manifests pin by digest.
  deletion_policy = "PREVENT"

  # A tag can never be moved to a different image.
  docker_config {
    immutable_tags = true
  }

  # infra/artifact-registry/cleanup-policy.json in HCL. KEEP beats DELETE, so the 5 most
  # recent versions of each image survive however old they are.
  cleanup_policy_dry_run = false

  cleanup_policies {
    id     = "keep-5-most-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 5
    }
  }

  cleanup_policies {
    id     = "delete-untagged-after-7d"
    action = "DELETE"
    condition {
      tag_state  = "UNTAGGED"
      older_than = "604800s"
    }
  }

  cleanup_policies {
    id     = "delete-tagged-after-30d"
    action = "DELETE"
    condition {
      tag_state  = "TAGGED"
      older_than = "2592000s"
    }
  }
}
