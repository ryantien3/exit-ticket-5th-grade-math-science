variable "project_id" {
  description = "GCP project ID. Set it in terraform.tfvars (gitignored)."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_id))
    error_message = "project_id must be a project ID (6-30 lowercase letters, digits or hyphens), not a name or number."
  }
}

variable "region" {
  description = "Region of the BigQuery dataset and of Pub/Sub message storage. Same as the cluster and Cloud SQL."
  type        = string
  default     = "us-east4"
}
