# Terraform

Four root modules ("layers"), each with its own state in the bucket `PROJECT_ID-tfstate`. Apply them in this order;
destroy in reverse.

| Layer | What | State prefix | Lifetime | Status |
|---|---|---|---|---|
| [`bootstrap/`](bootstrap/) | The state bucket | `bootstrap` | Forever | Done ([5A](../../docs/phase-5a-bootstrap.md)) |
| [`foundation/`](foundation/) | APIs, service accounts, IAM, secret containers, Artifact Registry ([5B](../../docs/phase-5b-foundation.md)); VPC, subnet, private services access ([5C](../../docs/phase-5c-network.md)) | `foundation` | Months | Done |
| [`data/`](data/) | Cloud SQL instance, database, IAM user; private IP on the VPC ([5D](../../docs/phase-5d-cloud-sql.md)) | `data` | Months, stopped between sessions | Done |
| [`cluster/`](cluster/) | GKE Autopilot: private nodes, DNS endpoint, node SA, Secret Manager add-on ([5E](../../docs/phase-5e-cluster.md)) | `cluster` | Created and destroyed every session | Done |

In any layer, on a new clone:
```bash
cp terraform.tfvars.example terraform.tfvars
terraform init -backend-config="bucket=$(gcloud config get project)-tfstate"
terraform plan
```

In `data/`, the default is **stopped**: start with `terraform apply -var="activation_policy=ALWAYS"`, stop with a
plain `terraform apply`, and pass the `-var` on every plan while it's running.

A session: `apply -var="activation_policy=ALWAYS"` in `data/` (start it first: ~11 min) and `apply` in `cluster/`
(~5 min) → work → `kubectl delete namespace exit-ticket` → `destroy` in `cluster/` → `apply` in `data/` (stop).

Rules: no project ID in committed files; never commit `terraform.tfvars`, state or plan files; commit
`.terraform.lock.hcl` (with `darwin_arm64` and `linux_amd64` checksums); `versions.tf` is identical in every layer.
