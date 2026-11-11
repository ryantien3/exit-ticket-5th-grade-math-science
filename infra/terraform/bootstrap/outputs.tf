output "state_bucket" {
  description = "Bucket name, for -backend-config and the state_bucket variable of later layers."
  value       = google_storage_bucket.tfstate.name
}

output "state_bucket_url" {
  description = "gs:// URL of the bucket."
  value       = google_storage_bucket.tfstate.url
}
