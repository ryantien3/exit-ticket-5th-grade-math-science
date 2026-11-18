# Credentials come from Application Default Credentials. No key files.
# No default_labels: the instance was imported, and the import must match reality exactly.
provider "google" {
  project = var.project_id
  region  = var.region
}

# The foundation layer's outputs (network, PSA range, api SA email), read from its state in
# the same bucket. Read-only: this layer can never change foundation's state.
data "terraform_remote_state" "foundation" {
  backend = "gcs"
  config = {
    bucket = "${var.project_id}-tfstate"
    prefix = "foundation"
  }
}

locals {
  foundation = data.terraform_remote_state.foundation.outputs
}
