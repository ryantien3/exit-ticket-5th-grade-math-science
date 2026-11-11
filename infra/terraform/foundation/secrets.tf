# Secret CONTAINERS only: name, replication, labels, IAM. Never a
# google_secret_manager_secret_version with a value: Terraform would store the value in state,
# in plain text. Versions come from scripts/create-app-secrets.sh (and, for the postgres
# password, an openssl pipe), so values only ever travel through pipes.
locals {
  secrets = {
    "session-secret"          = { labels = { app = "exit-ticket" } }
    "teacher-passcode"        = { labels = { app = "exit-ticket" } }
    "postgres-admin-password" = { labels = {} }
  }
}

resource "google_secret_manager_secret" "this" {
  for_each  = local.secrets
  secret_id = each.key
  labels    = each.value.labels

  # Deleting a secret destroys every version of its value.
  deletion_protection = true

  # Fixed at creation: changing it means a new secret.
  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }
}
