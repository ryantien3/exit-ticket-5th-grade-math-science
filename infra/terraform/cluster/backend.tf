# State in the bootstrap bucket, prefix "cluster". Pass the bucket at init:
#   terraform init -backend-config="bucket=$(gcloud config get project)-tfstate"
terraform {
  backend "gcs" {
    prefix = "cluster"
  }
}
