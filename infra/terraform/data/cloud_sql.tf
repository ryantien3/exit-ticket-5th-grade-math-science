# exit-ticket-db, created by hand in Phase 4A and imported in Phase 5D. Every value matches
# `gcloud sql instances describe` as it was; changing some of them (name, region, disk type)
# forces a REPLACEMENT, i.e. a new, empty instance. Any -/+ on this resource in a plan: stop.
resource "google_sql_database_instance" "main" {
  name             = "exit-ticket-db"
  region           = var.region
  database_version = "POSTGRES_16"

  # Terraform-side lock (state only): any plan that would destroy or replace the instance
  # fails. The API-side lock is settings.deletion_protection_enabled below.
  deletion_protection = true

  # A third lock, also state only (same as 5B/5C): with PREVENT in state, any destroy fails,
  # even if this block is deleted from the code.
  deletion_policy = "PREVENT"

  # No root_password: the postgres password lives only in Secret Manager, never in state.

  settings {
    edition           = "ENTERPRISE" # Postgres 16 defaults to Enterprise Plus, which has no db-f1-micro
    tier              = "db-f1-micro"
    availability_type = "ZONAL"
    pricing_plan      = "PER_USE"
    activation_policy = var.activation_policy

    disk_type       = "PD_SSD"
    disk_size       = 10
    disk_autoresize = false

    # API-side lock: gcloud, the Console and Terraform are all refused a delete.
    deletion_protection_enabled = true

    # Only the Auth Proxy and Cloud SQL connectors may connect (mutual TLS, IAM-authorized).
    connector_enforcement = "REQUIRED"

    # Google turned this on by default (Dataplex catalogs the instance's metadata; no data
    # leaves it, no charge). Kept as found so the import shows no change.
    enable_dataplex_integration = true

    user_labels = {
      app = "exit-ticket"
      env = "dev"
    }

    location_preference {
      zone = "us-east4-a"
    }

    # Sunday (the API counts Monday as 1), 08:00 UTC.
    maintenance_window {
      day  = 7
      hour = 8
    }

    backup_configuration {
      enabled                        = true
      start_time                     = "07:00" # UTC; only runs if the instance is running then
      point_in_time_recovery_enabled = false
      transaction_log_retention_days = 7

      backup_retention_settings {
        retained_backups = 7
        retention_unit   = "COUNT"
      }
    }

    database_flags {
      name  = "cloudsql.iam_authentication"
      value = "on"
    }

    ip_configuration {
      # Public IP with no authorized networks: unreachable except through the proxy. Kept
      # (Phase 5D) so the Mac's proxy still works; pods will use the private IP.
      ipv4_enabled = true

      # Private IP on exit-ticket-vpc, through private services access (foundation layer).
      # Adding it RESTARTS the instance, and it can never be removed afterwards.
      private_network    = local.foundation.network_id
      allocated_ip_range = local.foundation.psa_range_name

      ssl_mode       = "ALLOW_UNENCRYPTED_AND_ENCRYPTED"
      server_ca_mode = "GOOGLE_MANAGED_INTERNAL_CA"
    }
  }
}

resource "google_sql_database" "exitticket" {
  name      = "exitticket"
  instance  = google_sql_database_instance.main.name
  charset   = "UTF8"
  collation = "en_US.UTF8"

  # Deleting this block would otherwise DROP DATABASE exitticket and every table in it.
  deletion_policy = "PREVENT"
}

# The app's IAM database user. Its name is the api service account's email without
# ".gserviceaccount.com" (Postgres role names max out at 63 characters). No password:
# the proxy logs in with a short-lived IAM token.
resource "google_sql_user" "api" {
  name     = trimsuffix(local.foundation.api_sa_email, ".gserviceaccount.com")
  instance = google_sql_database_instance.main.name
  type     = "CLOUD_IAM_SERVICE_ACCOUNT"

  # Deleting the user would break the app's login.
  deletion_policy = "PREVENT"
}

# Not managed here, on purpose:
# - the postgres user and its password (a password in Terraform is written to state in plain
#   text; it lives only in Secret Manager)
# - the GRANTs inside Postgres (CONNECT, USAGE + CREATE on schema public): the Google provider
#   talks to the Cloud SQL Admin API, not to Postgres. They survive because nothing is rebuilt.
