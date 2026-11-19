# Credentials come from Application Default Credentials. No key files.
provider "google" {
  project = var.project_id
  region  = var.region

  # Every resource this layer creates gets these labels (where the resource supports labels).
  default_labels = {
    app   = "exit-ticket"
    env   = "dev"
    layer = "analytics"
  }
}

# The foundation layer's outputs (api SA email), read from its state in the same bucket.
# Read-only: this layer can never change foundation's state. Dependencies flow one way:
# foundation never reads this layer.
data "terraform_remote_state" "foundation" {
  backend = "gcs"
  config = {
    bucket = "${var.project_id}-tfstate"
    prefix = "foundation"
  }
}

# The project number, for the Pub/Sub service agent's email. Looked up, never hard-coded.
data "google_project" "this" {}

locals {
  foundation = data.terraform_remote_state.foundation.outputs

  # Google-managed account that runs BigQuery subscriptions and dead-lettering for this project.
  pubsub_service_agent = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}
