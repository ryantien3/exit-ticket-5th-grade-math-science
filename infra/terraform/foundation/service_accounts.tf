# The app's identity: the api pods and the migrate Job act as it through Workload Identity,
# for the Cloud SQL Auth Proxy (IAM database login) and the Secret Manager add-on.
resource "google_service_account" "api" {
  account_id   = "exit-ticket-api"
  display_name = "Exit Ticket API (pods)"
  description  = "Pod identity for the api and migrate Job: Cloud SQL IAM login"

  # A recreated SA gets a new unique_id, and every grant to the old one breaks.
  deletion_policy = "PREVENT"
}

# The GKE nodes' identity: pull images from one repository, write logs and metrics.
resource "google_service_account" "nodes" {
  account_id   = "exit-ticket-nodes"
  display_name = "Exit Ticket GKE nodes"
  description  = "Node identity: pull images, write logs and metrics"

  deletion_policy = "PREVENT"
}
