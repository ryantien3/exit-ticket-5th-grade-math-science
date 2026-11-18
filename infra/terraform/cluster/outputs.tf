output "cluster_name" {
  description = "GKE cluster name."
  value       = google_container_cluster.main.name
}

output "dns_endpoint" {
  description = "The control plane's DNS-based endpoint (what kubectl on the Mac talks to)."
  value       = google_container_cluster.main.control_plane_endpoints_config[0].dns_endpoint_config[0].endpoint
}

output "get_credentials" {
  description = "Command to point kubectl at this cluster through the DNS endpoint."
  value       = "gcloud container clusters get-credentials ${google_container_cluster.main.name} --region=${var.region} --dns-endpoint"
}

# Passed through from the data layer, for the proxy sidecar's arguments.
output "cloud_sql_connection_name" {
  description = "Cloud SQL connection name (PROJECT_ID:REGION:INSTANCE)."
  value       = local.data.connection_name
}

output "cloud_sql_private_ip" {
  description = "Cloud SQL private IP; the proxy with --private-ip should dial this."
  value       = local.data.private_ip
}
