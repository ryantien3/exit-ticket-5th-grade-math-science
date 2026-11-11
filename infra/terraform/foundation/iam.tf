# Every grant here is an *_iam_member: it adds one member to one role and leaves every other
# member alone. Never *_iam_policy (replaces the whole policy) or *_iam_binding (owns every
# member of a role): either can silently remove grants made outside this file, including
# Google's own service agents and your Owner role.

# --- Project roles ----------------------------------------------------------------------

# Cloud SQL has no per-instance IAM, so these are granted on the project.
resource "google_project_iam_member" "api_cloudsql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = google_service_account.api.member
}

resource "google_project_iam_member" "api_cloudsql_instance_user" {
  project = var.project_id
  role    = "roles/cloudsql.instanceUser"
  member  = google_service_account.api.member
}

# Logs, metrics and node metadata for GKE nodes.
resource "google_project_iam_member" "nodes_default_node_sa" {
  project = var.project_id
  role    = "roles/container.defaultNodeServiceAccount"
  member  = google_service_account.nodes.member
}

# --- Who may act as the api service account ---------------------------------------------

# The Kubernetes SA exit-ticket/api acts as exit-ticket-api (Workload Identity).
resource "google_service_account_iam_member" "api_workload_identity" {
  service_account_id = google_service_account.api.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[exit-ticket/api]"
}

# You, to seed from the Mac as the app (cloud-sql-proxy --impersonate-service-account).
resource "google_service_account_iam_member" "api_token_creator_admin" {
  service_account_id = google_service_account.api.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = var.admin_member
}

# --- Resource-level grants --------------------------------------------------------------

# The api reads two secrets. Not postgres-admin-password: that one has no bindings at all.
resource "google_secret_manager_secret_iam_member" "api_accessor" {
  for_each  = toset(["session-secret", "teacher-passcode"])
  secret_id = google_secret_manager_secret.this[each.key].id
  role      = "roles/secretmanager.secretAccessor"
  member    = google_service_account.api.member
}

# Nodes pull images from this one repository only.
resource "google_artifact_registry_repository_iam_member" "nodes_reader" {
  location   = google_artifact_registry_repository.exit_ticket.location
  repository = google_artifact_registry_repository.exit_ticket.name
  role       = "roles/artifactregistry.reader"
  member     = google_service_account.nodes.member
}

# roles/editor on the Compute Engine default SA (granted automatically with the Compute API)
# was removed in Phase 5B: imported as a google_project_iam_member, then this block was
# deleted, so the apply removed exactly that one member. Don't re-add it; give a workload
# its own service account instead.
