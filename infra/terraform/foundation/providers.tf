# Credentials come from Application Default Credentials. No key files.
# No default_labels here (unlike bootstrap): every resource in this layer was imported, and
# the import must match reality exactly. Labels are set per resource.
provider "google" {
  project = var.project_id
  region  = var.region
}

# Project number, for the Compute Engine default service account's email.
data "google_project" "this" {}
