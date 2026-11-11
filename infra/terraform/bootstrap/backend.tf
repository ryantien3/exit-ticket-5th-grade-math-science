# State lives in the bucket this layer creates. The bucket name isn't written here
# (backend blocks can't use variables, and no project ID is committed); pass it at init:
#   terraform init -backend-config="bucket=$(gcloud config get project)-tfstate"
terraform {
  backend "gcs" {
    prefix = "bootstrap"
  }
}
