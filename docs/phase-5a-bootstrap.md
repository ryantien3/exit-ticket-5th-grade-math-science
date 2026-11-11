# Phase 5A — Terraform bootstrap and remote state

Phase 5A sets up the Terraform structure for all of Phase 5 and creates the **GCS bucket that holds Terraform state**.
The bucket was created with local state, then its own state was migrated into it, so from here on every layer's
state lives in one versioned, locked, private bucket and nothing state-related ever touches git. Decisions come from
the [pre-check](phase-5-precheck.md).

| Part | What | PR |
|---|---|---|
| **Pre-check** | Known-good start, decisions (a)–(e) | #19, #20 |
| **5A** | Layer layout; `bootstrap` layer: state bucket (versioning, uniform access, public access prevention, lifecycle, three delete locks); state migrated into it | (this PR) |

> **About the commands in this doc.** Code blocks contain commands only, no `#` comments: in zsh, `#` is not a
> comment unless `setopt interactivecomments` is on. **Run them from `infra/terraform/bootstrap`** unless a block
> starts with `cd`. The project comes from `$(gcloud config get project)` or the gitignored `terraform.tfvars`, so no
> project ID is committed.

> **Placeholders**
>
> | Placeholder | Meaning |
> |---|---|
> | `PROJECT_ID` | Your GCP project ID (`gcloud config get project`) |
> | `LOCK_ID` | The ID printed in an `Error acquiring the state lock` message |

**Contents**

1. [Concepts](#1-concepts)
2. [Layout: four layers](#2-layout-four-layers)
3. [What exists, and what it costs](#3-what-exists-and-what-it-costs)
4. [Build from scratch](#4-build-from-scratch)
5. [Reading the plan](#5-reading-the-plan)
6. [Migrating the state into the bucket](#6-migrating-the-state-into-the-bucket)
7. [Verification checklist](#7-verification-checklist)
8. [Troubleshooting](#8-troubleshooting)
9. [Teardown](#9-teardown)
10. [Known gaps](#10-known-gaps)
11. [Lessons learned](#11-lessons-learned)
12. [Interview talking points](#12-interview-talking-points)
13. [ACE exam notes](#ace-exam-notes)

---

## 1. Concepts

### State

A JSON file mapping each block in the code (an **address** like `google_storage_bucket.tfstate`) to the real
resource's ID and **every attribute** Terraform last saw. Without it, Terraform can't tell what already exists.

- **It contains secrets in plain text.** Any attribute (a generated password, a secret version's data, a cluster
  certificate) is stored as-is. `sensitive = true` only hides values in terminal output. So state is a secret: never
  in git, only where IAM controls who reads it. That's why secret values stay outside Terraform here.
- **Lineage** is a random ID created with a state and kept for its whole life; **serial** goes up on every write.
  Same lineage = same state history.

### Local vs remote

| | Local `terraform.tfstate` | Remote (GCS object) |
|---|---|---|
| Survives a lost laptop | No | Yes (versioned, replicated) |
| Shared with Cloud Build (Phase 7) | No | Yes |
| Two runs at once | Nothing stops them | **Locked** |
| Risk of committing it | Real | None |

### Locking

Before anything that may write state, the GCS backend creates `<prefix>/default.tflock`. GCS creates an object
"only if it doesn't exist" atomically, so exactly one run wins; the other fails with `Error acquiring the state
lock`. A crashed run can leave the lock behind: `terraform force-unlock LOCK_ID`, **only** when nothing else is
running. The backend needs no extra database for this.

### The chicken-and-egg problem

The bucket must exist before it can hold state, including its own. The `bootstrap` layer creates the bucket with
**local** state, then a `backend "gcs"` block and `terraform init -migrate-state` copy that state into the bucket.

### `plan` and `apply`

- `plan` reads the real resources, compares them with the code and lists actions: `+` create, `~` update in place,
  `-` destroy, **`-/+` replace** (the one to stop at). It changes nothing.
- `apply` makes the changes. With a saved plan (`plan -out=tfplan`, then `apply tfplan`) it does **exactly** what
  was reviewed, **without asking `yes`**, and refuses if anything changed since (`Saved plan is stale`). Plan files
  contain state data, so they're gitignored.

### Pinning

| Pin | Where | Value | Why |
|---|---|---|---|
| Terraform CLI | `required_version` in `versions.tf` | `~> 1.17.0` (1.17.x) | Newer Terraform can write state an older one can't read; the Mac and Cloud Build must agree |
| Google provider | `required_providers` | `~> 8.11` (8.11+, never 9.x) | A new major version renames or removes arguments |
| Exact provider build | `.terraform.lock.hcl` (committed) | `8.11.0` + checksums | Everyone gets the same build; tampered downloads are rejected; upgrades are deliberate (`init -upgrade`) |

The lock file only records checksums for the platform `init` ran on. **`terraform providers lock
-platform=darwin_arm64 -platform=linux_amd64`** adds Cloud Build's platform; without it Phase 7 fails on a checksum
mismatch.

---

## 2. Layout: four layers

```
infra/terraform/
├── bootstrap/    state bucket                                                       prefix bootstrap   forever
├── foundation/   APIs, SAs, IAM, secret containers, Artifact Registry, VPC, PSA     prefix foundation  months, ~$0
├── data/         Cloud SQL instance, database, IAM user (activation_policy var)     prefix data        months, stopped
└── cluster/      GKE Autopilot (+ Cloud NAT only if needed)                         prefix cluster     each session
```

Each layer is its own **root module** with its own state in the same bucket. Each has `versions.tf` (identical in
every layer), `providers.tf`, `backend.tf`, `variables.tf`, resources, `outputs.tf`, `terraform.tfvars.example`
and `.terraform.lock.hcl`.

- **Why layers:** blast radius and lifetime. `terraform destroy` in `cluster/` can only touch the cluster, which is
  what the strict teardown policy needs; the network, identities and database are in other states.
- **Sharing values:** a layer `output`s what others need (VPC ID, SA emails); later layers read it with `data
  "terraform_remote_state"`. Dependencies flow one way: foundation → data, foundation → cluster.
- **Private services access goes in foundation:** free, long-lived, and the peering can't be deleted while Cloud SQL
  uses it.
- **No custom modules:** modules pay off when something repeats (dev/prod, several regions). One environment with
  each resource once doesn't need them; `moved` blocks can introduce them later without recreating anything.
- **No project ID in git:** the `backend` block can't use variables, so it holds only `prefix`; the bucket is passed
  with `-backend-config` at `init` and lands in the gitignored `.terraform/`.

---

## 3. What exists, and what it costs

| Resource | Settings | Cost |
|---|---|---|
| Bucket `PROJECT_ID-tfstate` | `US-EAST4`, Standard; uniform bucket-level access; public access prevention **enforced**; versioning on; lifecycle: delete a noncurrent version once 10 newer exist; soft delete 7 days (default); labels `app`, `env`, `managed-by`, `goog-terraform-provisioned` | A few KB: **effectively $0** (us-east4 isn't an Always Free region, but the bill rounds to zero) |
| `bootstrap/default.tfstate` | This layer's state | Included |

Three locks against deleting it:

| Lock | Enforced by | Stops |
|---|---|---|
| `force_destroy = false` | The Storage API | Deleting a bucket that still has objects |
| `deletion_policy = "PREVENT"` | The provider, from **state** | A destroy, even if the block is deleted from the code |
| `lifecycle { prevent_destroy = true }` | Terraform, from **code** | Planning a destroy |

---

## 4. Build from scratch

Prerequisites: Terraform 1.17.x (`brew install hashicorp/tap/terraform`), ADC with a quota project (pre-check §3),
the `storage` API enabled.

```bash
cd ~/projects/exit-ticket-5th-grade-math-science/infra/terraform/bootstrap
cp terraform.tfvars.example terraform.tfvars
```
Put the project ID in `terraform.tfvars`. Then, **with `backend.tf` moved aside** (first build only, since the bucket
doesn't exist yet):
```bash
mv backend.tf backend.tf.off
terraform init
terraform providers lock -platform=darwin_arm64 -platform=linux_amd64
terraform fmt -check
terraform validate
terraform plan -out=tfplan
terraform apply tfplan
rm tfplan
mv backend.tf.off backend.tf
terraform init -migrate-state -backend-config="bucket=$(gcloud config get project)-tfstate"
```
Answer `yes` to copying the state, then [§7](#7-verification-checklist), then delete the local leftovers:
```bash
rm terraform.tfstate terraform.tfstate.backup
```

**On a new clone, once the bucket exists** (the usual case): just
```bash
terraform init -backend-config="bucket=$(gcloud config get project)-tfstate"
terraform plan
```

The gcloud equivalent of the bucket, for the exam:
```bash
gcloud storage buckets create gs://PROJECT_ID-tfstate --location=us-east4 --default-storage-class=STANDARD --uniform-bucket-level-access --public-access-prevention
gcloud storage buckets update gs://PROJECT_ID-tfstate --versioning
```

---

## 5. Reading the plan

The first plan: `Plan: 1 to add, 0 to change, 0 to destroy.` What to look at:

| Line | Meaning |
|---|---|
| `# google_storage_bucket.tfstate will be created` | The **address**: type + name. Renaming it later needs a `moved` block, or it's destroy + create |
| `(known after apply)` | Set by Google at creation: `id`, `url`, `self_link`, `time_created`, `project_number` |
| `location = "US-EAST4"` | `upper(var.region)`: written the way the API returns it, so there's no fake diff later |
| `labels` / `terraform_labels` / `effective_labels` | Labels on this resource / plus the provider's `default_labels` (what Terraform manages) / everything on the real bucket. `goog-terraform-provisioned` is added by the provider |
| `deletion_policy = "PREVENT"` | Not in the first draft: it appeared in the plan as `"DELETE"` (a new provider 8.x attribute), was looked up in the schema, and set |
| `lifecycle_rule { … with_state = "ARCHIVED", num_newer_versions = 10 }` | Noncurrent versions only; the live state is never touched. The API calls it `isLive: false` |
| `soft_delete_policy (known after apply)` | Google's default: 7 days |
| `rpo` | Only for dual-region buckets |

The second plan, after apply and again after migration: **`No changes.`** The real bucket matches the code.

---

## 6. Migrating the state into the bucket

[`backend.tf`](../infra/terraform/bootstrap/backend.tf) holds only `prefix = "bootstrap"`.

```bash
export CLOUDSDK_PYTHON=/opt/homebrew/bin/python3.13
terraform init -migrate-state -backend-config="bucket=$(gcloud config get project)-tfstate"
```

What happened (as observed, not as expected):
1. Terraform saw the backend change from local to `gcs`, connected with ADC, found no state at the prefix and asked
   to copy. `yes`.
2. It wrote **`bootstrap/default.tfstate`** to the bucket (two object versions: create, then contents). The serial
   went from **2 to 3**: the migration is a write.
3. It **emptied** the local `terraform.tfstate` (0 bytes) and kept the old contents as
   `terraform.tfstate.backup`. Both are gitignored.
4. `.terraform/terraform.tfstate` now records the backend (type, bucket, prefix). **It isn't state**, despite the
   name.

Proving the copy: lineage `a720f821-…` in both the bucket copy and the backup; resources and outputs identical;
`plan` → `No changes` against the remote state. Then the local leftovers were deleted: keeping them risks a later
run starting from stale state, and the bucket keeps 10 old versions plus 7 days of soft delete.

---

## 7. Verification checklist

| Check | Command | Healthy |
|---|---|---|
| Format | `terraform fmt -check` | No output, exit 0 |
| Syntax and types | `terraform validate` | `Success! The configuration is valid.` |
| Code matches reality | `terraform plan` | `No changes.` |
| Terraform's view | `terraform state list` | `google_storage_bucket.tfstate` |
| Backend in use | `python3 -c "import json; d=json.load(open('.terraform/terraform.tfstate'))['backend']; print(d['type'], d['config']['prefix'])"` | `gcs bootstrap` |
| State object | `gcloud storage ls -l gs://$(gcloud config get project)-tfstate/bootstrap/` | `default.tfstate` ~4.5 KB; **no live `.tflock`** |
| Versions | `gcloud storage ls -a gs://$(gcloud config get project)-tfstate/bootstrap/` | Objects with `#GENERATION`; old `.tflock` versions are normal |
| Bucket settings | `gcloud storage buckets describe gs://$(gcloud config get project)-tfstate --format="yaml(location,default_storage_class,uniform_bucket_level_access,public_access_prevention,versioning_enabled,lifecycle_config,soft_delete_policy,labels)"` | `US-EAST4`, `STANDARD`, `true`, `enforced`, `true`, one Delete rule (`isLive: false`, `numNewerVersions: 10`), `604800`, four labels |
| Same state | `terraform state pull \| python3 -c "import json,sys; d=json.load(sys.stdin); print(d['lineage'], d['serial'])"` | The lineage from the first apply |
| Nothing local | `ls -a` | No `terraform.tfstate*`, no `tfplan` |
| Nothing committed | `git status --ignored infra/terraform` | `.terraform/`, `terraform.tfvars`, state files under `!!` (ignored) |

Verified 2026-11-11: all healthy.

---

## 8. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `gcloud crashed (CommandLoadFailure) … module 'ssl' has no attribute 'PROTOCOL_TLSv1'` on `gcloud storage` | gcloud runs on Homebrew's Python 3.14, built on OpenSSL 4, which dropped TLS 1.0; gcloud's bundled urllib3 still references it | `export CLOUDSDK_PYTHON=/opt/homebrew/bin/python3.13` in that window; later try `brew upgrade --cask gcloud-cli` |
| `Backend initialization required` | `backend.tf` added or changed, or a fresh clone | `terraform init -backend-config="bucket=$(gcloud config get project)-tfstate"` |
| `init` prompts for `bucket` | `-backend-config` left out | Ctrl+C and pass it |
| `Error acquiring the state lock` | Another run holds it, or one crashed | Wait; if certain nothing runs, `terraform force-unlock LOCK_ID` |
| `Saved plan is stale` | Something changed since `plan -out` | Plan again |
| `Instance cannot be destroyed` / `deletion_policy is PREVENT` | The delete locks | Intended. See [§9](#9-teardown) |
| `409 … bucket name not available` | Bucket names are global | Another name (the project ID prefix makes this unlikely) |
| Checksum mismatch on another machine | Lock file lacks that platform | `terraform providers lock -platform=…` and commit |
| `could not find default credentials` | ADC missing or expired | `gcloud auth application-default login`, then `set-quota-project` |

---

## 9. Teardown

**None.** The bucket costs effectively nothing and holds every layer's state; it stays for the life of the project.
The per-session teardown policy applies to the `cluster` layer (and stopping Cloud SQL), not here.

Deleting it for good, only at the very end of the project and only after every other layer is destroyed: migrate
the state back to local (remove `backend.tf`, `terraform init -migrate-state`), set `deletion_policy = "DELETE"` and
`force_destroy = true`, remove `prevent_destroy`, `apply`, then `destroy`.

---

## 10. Known gaps

| Gap | Why it's acceptable now | Later |
|---|---|---|
| **Anyone with Editor or Owner on the project can read state**, including the Compute Engine default SA (Editor includes `storage.objects.get`) | No state holds secrets yet; only you use the project | Decision (e): remove Editor from the default SA. Phase 7: grant Cloud Build's SA access on this bucket only |
| Old `.tflock` versions accumulate | Capped at 10 per name by the lifecycle rule, a few hundred bytes each | A rule for `.tflock` with `days_since_noncurrent_time` if it ever matters |
| Google-managed encryption, not CMEK | Default encryption at rest is fine for synthetic data | CMEK via Cloud KMS for real data |
| The `storage` API this layer depends on isn't managed by it | It's managed by foundation (`google_project_service`), which needs the bucket first | — (the chicken and egg again) |
| gcloud `storage` commands need `CLOUDSDK_PYTHON` | One-line workaround | Upgrade gcloud |

---

## 11. Lessons learned

| Session | What happened | Lesson |
|---|---|---|
| 5A | The plan showed `deletion_policy = "DELETE"`, never written | Read every line of a plan, not only the ones you wrote; look unknown attributes up in the schema (`terraform providers schema -json`). Provider 8.x adds it |
| 5A | The first lock file only had `darwin_arm64` checksums | Add CI platforms with `terraform providers lock` before committing |
| 5A | `gcloud storage buckets describe` crashed on `PROTOCOL_TLSv1` | Not every gcloud group loads the same libraries; the first use of a new group can surface an environment problem. Check another way (`terraform plan` read the bucket fine) |
| 5A | Expected: local state untouched, serial 2. Observed: local file emptied, `.backup` kept, serial 3 | Verify what the tool did, not what you predicted; compare **lineage** and contents, not serial |
| 5A | `gcloud storage ls -a` showed 4 `.tflock` versions | In a versioned bucket every deleted object stays as a noncurrent version, lock files included |

---

## 12. Interview talking points

**1. "State is a secret, so it lives in a locked, versioned, private bucket."** Uniform bucket-level access, public
access prevention enforced, 10 old versions, 7-day soft delete; never in git, and no project ID committed either.

**2. "I solved the bootstrap problem in code."** The bucket is a Terraform resource whose own state lives inside it,
created with local state and migrated, with three independent locks against deleting it.

**3. "Layers by lifetime and blast radius."** Separate states for the bucket, long-lived foundation, the stateful
database and a cluster destroyed every session; values flow one way through outputs.

**4. "Reproducible tooling."** Terraform and the provider pinned, the lock file committed with checksums for both
my Mac and the CI platform.

---

## ACE exam notes

**Cloud Storage**
- Location types: **region** (lowest latency and cost for one region), **dual-region**, **multi-region**; storage
  classes **Standard, Nearline (30 days), Coldline (90), Archive (365)**: colder = cheaper storage, retrieval fees and
  minimum storage durations.
- **Uniform bucket-level access**: IAM only, ACLs disabled. **Public access prevention**: blocks `allUsers` and
  `allAuthenticatedUsers`.
- **Object Versioning** keeps noncurrent versions (identified by generation numbers); **lifecycle rules** delete or
  change class by age, version count, live state, prefix/suffix. **Soft delete** (default 7 days) recovers deleted
  objects. **Retention policies / bucket lock** are for compliance (can't delete before a period).
- Bucket names are **globally unique**. `gcloud storage` replaces `gsutil`.

**Infrastructure as code**
- **Terraform** is Google's recommended IaC tool; **Infrastructure Manager** runs Terraform as a managed service and
  stores state for you; Deployment Manager is deprecated.
- Remote state in a GCS bucket with versioning; the GCS backend locks state natively.
- Know the workflow: `init` → `plan` → `apply` (→ `destroy`).

**Credentials**
- Terraform authenticates with **Application Default Credentials**, not `gcloud auth login`; no service account
  keys. On Google Cloud (Cloud Build, GKE) ADC comes from the metadata server.
