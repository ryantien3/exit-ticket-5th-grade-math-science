# Read by the cluster layer through terraform_remote_state (prefix "data").

output "connection_name" {
  description = "PROJECT_ID:REGION:INSTANCE, for the Auth Proxy sidecar and connectors."
  value       = google_sql_database_instance.main.connection_name
}

output "private_ip" {
  description = "Private IP on exit-ticket-vpc (from the PSA range). Pods connect with the proxy's --private-ip."
  value       = google_sql_database_instance.main.private_ip_address
}

output "database_name" {
  description = "The app's database."
  value       = google_sql_database.exitticket.name
}

output "db_iam_user" {
  description = "The app's IAM database user (the api SA email without .gserviceaccount.com)."
  value       = google_sql_user.api.name
}
