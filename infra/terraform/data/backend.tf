# State in the bootstrap bucket, prefix "data". Pass the bucket at init:
#   terraform init -backend-config="bucket=$(gcloud config get project)-tfstate"
terraform {
  backend "gcs" {
    prefix = "data"
  }
}
