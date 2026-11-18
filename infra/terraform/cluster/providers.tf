# Credentials come from Application Default Credentials. No key files.
provider "google" {
  project = var.project_id
  region  = var.region
}

# Read-only views of the two longer-lived layers. Dependencies flow one way: this layer reads
# them, they never read this one, so destroying the cluster can't touch them.
data "terraform_remote_state" "foundation" {
  backend = "gcs"
  config = {
    bucket = "${var.project_id}-tfstate"
    prefix = "foundation"
  }
}

data "terraform_remote_state" "data" {
  backend = "gcs"
  config = {
    bucket = "${var.project_id}-tfstate"
    prefix = "data"
  }
}

locals {
  foundation = data.terraform_remote_state.foundation.outputs
  data       = data.terraform_remote_state.data.outputs
}
