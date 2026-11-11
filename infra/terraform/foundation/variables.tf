variable "project_id" {
  description = "GCP project ID. Set it in terraform.tfvars (gitignored)."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_id))
    error_message = "project_id must be a project ID (6-30 lowercase letters, digits or hyphens), not a name or number."
  }
}

variable "region" {
  description = "Region for regional resources (Artifact Registry, secret replicas)."
  type        = string
  default     = "us-east4"
}

variable "admin_member" {
  description = "You, as an IAM member (\"user:EMAIL\"). Allowed to impersonate the api SA to seed from the Mac. Set it in terraform.tfvars."
  type        = string

  validation {
    condition     = startswith(var.admin_member, "user:")
    error_message = "admin_member must look like \"user:EMAIL\"."
  }
}
