# Every API the project uses: Phase 1's thirteen, plus what later phases needed.
locals {
  services = toset([
    "artifactregistry.googleapis.com",
    "bigquery.googleapis.com",
    "cloudbuild.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "compute.googleapis.com",
    "container.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "pubsub.googleapis.com",
    "secretmanager.googleapis.com",
    "servicenetworking.googleapis.com",
    "serviceusage.googleapis.com",
    "sqladmin.googleapis.com",
    "storage.googleapis.com",
  ])
}

resource "google_project_service" "this" {
  for_each = local.services
  service  = each.key

  # Removing an API from this list (or destroying this layer) only stops Terraform managing
  # it; the API stays on. Turning an API off can break everything that uses it, including
  # resources in other layers and the state bucket itself.
  disable_on_destroy         = false
  disable_dependent_services = false
}
