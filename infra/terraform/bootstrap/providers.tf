# Credentials come from Application Default Credentials (gcloud auth application-default login).
# No key files.
provider "google" {
  project = var.project_id
  region  = var.region

  # Added to every resource in this layer that supports labels.
  default_labels = {
    app        = "exit-ticket"
    env        = "dev"
    managed-by = "terraform"
  }
}
