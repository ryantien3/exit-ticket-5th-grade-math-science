variable "project_id" {
  description = "GCP project ID. Set it in terraform.tfvars (gitignored)."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_id))
    error_message = "project_id must be a project ID (6-30 lowercase letters, digits or hyphens), not a name or number."
  }
}

variable "region" {
  description = "Region for the state bucket."
  type        = string
  default     = "us-east4"
}

variable "noncurrent_versions_to_keep" {
  description = "Old versions of each state file to keep for rollback."
  type        = number
  default     = 10
}
