variable "project_id" {
  description = "GCP project ID. Set it in terraform.tfvars (gitignored)."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_id))
    error_message = "project_id must be a project ID (6-30 lowercase letters, digits or hyphens), not a name or number."
  }
}

variable "region" {
  description = "Region of the Cloud SQL instance."
  type        = string
  default     = "us-east4"
}

variable "activation_policy" {
  description = "ALWAYS = running (compute bills), NEVER = stopped. The default stops it: start with -var=\"activation_policy=ALWAYS\", stop with a plain apply."
  type        = string
  default     = "NEVER"

  validation {
    condition     = contains(["ALWAYS", "NEVER"], var.activation_policy)
    error_message = "activation_policy must be ALWAYS or NEVER."
  }
}
