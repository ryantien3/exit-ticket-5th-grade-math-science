# Same file in every layer: bump them together.
terraform {
  # 1.17.x only. State written by a newer Terraform can't be read by an older one,
  # so the Mac and Cloud Build must agree.
  required_version = "~> 1.17.0"

  required_providers {
    google = {
      source = "hashicorp/google"
      # 8.11 or newer, never 9.x. The exact build is in .terraform.lock.hcl.
      version = "~> 8.11"
    }
  }
}
