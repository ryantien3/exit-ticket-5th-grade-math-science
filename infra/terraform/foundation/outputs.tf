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

output "network_id" {
  description = "VPC ID (projects/.../global/networks/exit-ticket-vpc), for the cluster and Cloud SQL private_network."
  value       = google_compute_network.vpc.id
}

output "network_name" {
  description = "VPC name."
  value       = google_compute_network.vpc.name
}

output "subnet_id" {
  description = "us-east4 subnet ID, for the cluster."
  value       = google_compute_subnetwork.us_east4.id
}

output "subnet_name" {
  description = "us-east4 subnet name."
  value       = google_compute_subnetwork.us_east4.name
}

output "pods_range_name" {
  description = "Secondary range for Pod IPs (cluster ip_allocation_policy)."
  value       = "pods"
}

output "services_range_name" {
  description = "Secondary range for Service IPs (cluster ip_allocation_policy)."
  value       = "services"
}

output "psa_range_name" {
  description = "Private services access reserved range (Cloud SQL allocated_ip_range)."
  value       = google_compute_global_address.psa.name
}

output "psa_peering" {
  description = "Name of the VPC peering to Google's service producer network."
  value       = google_service_networking_connection.psa.peering
}
