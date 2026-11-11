# Terraform

Four root modules ("layers"), each with its own state in the bucket `PROJECT_ID-tfstate`. Apply them in this order;
destroy in reverse.

| Layer | What | State prefix | Lifetime | Status |
|---|---|---|---|---|
| [`bootstrap/`](bootstrap/) | The state bucket | `bootstrap` | Forever | Done ([5A](../../docs/phase-5a-bootstrap.md)) |
| [`foundation/`](foundation/) | APIs, service accounts, IAM, secret containers, Artifact Registry ([5B](../../docs/phase-5b-foundation.md)); VPC, private services access (5C) | `foundation` | Months | Part 1 done |
| `data/` | Cloud SQL (stop/start with `activation_policy`) | `data` | Months, stopped between sessions | |
| `cluster/` | GKE Autopilot | `cluster` | Created and destroyed every session | |

In any layer, on a new clone:
```bash
cp terraform.tfvars.example terraform.tfvars
terraform init -backend-config="bucket=$(gcloud config get project)-tfstate"
terraform plan
```

Rules: no project ID in committed files; never commit `terraform.tfvars`, state or plan files; commit
`.terraform.lock.hcl` (with `darwin_arm64` and `linux_amd64` checksums); `versions.tf` is identical in every layer.
