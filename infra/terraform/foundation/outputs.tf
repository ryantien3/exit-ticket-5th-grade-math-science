# Read by later layers through terraform_remote_state (prefix "foundation").

output "project_number" {
  description = "Project number (Workload Identity pool, default service account emails)."
  value       = data.google_project.this.number
}

output "api_sa_email" {
  description = "exit-ticket-api: the KSA annotation, the Cloud SQL IAM database user."
  value       = google_service_account.api.email
}

output "nodes_sa_email" {
  description = "exit-ticket-nodes: the GKE cluster's node service account."
  value       = google_service_account.nodes.email
}

output "artifact_registry_repo" {
  description = "Image path prefix, e.g. <this>/api@sha256:..."
  value       = "${google_artifact_registry_repository.exit_ticket.location}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.exit_ticket.repository_id}"
}

output "secret_ids" {
  description = "Secret resource IDs by name (containers only; no values)."
  value       = { for name, s in google_secret_manager_secret.this : name => s.id }
}
