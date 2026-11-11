# State in the bootstrap bucket, prefix "foundation". Pass the bucket at init:
#   terraform init -backend-config="bucket=$(gcloud config get project)-tfstate"
terraform {
  backend "gcs" {
    prefix = "foundation"
  }
}
