# Phase 5B — Terraform foundation, part 1: importing what was built by hand

Phase 5B brings the long-lived resources built with gcloud in Phases 1–4 under Terraform, in the `foundation` layer:
16 APIs, the two service accounts, their IAM grants, the three Secret Manager secrets (containers only, never
values) and the Artifact Registry repository with its cleanup policy. All 30 were **imported**, not recreated:
nothing was deleted, and the first plan after the import said `No changes`. It also closes a Phase 4 known gap:
**Editor is gone from the Compute Engine default service account**, removed through Terraform after checking that
nothing depends on it.

| Part | What | PR |
|---|---|---|
| **5A** | State bucket, layer layout | #21 |
| **5B** | `foundation` layer: 30 imports, delete protections, Editor removed from the default SA, `create-app-secrets.sh` adds versions to Terraform's containers | (this PR) |

> **About the commands in this doc.** Code blocks contain commands only, no `#` comments: in zsh, `#` is not a
> comment unless `setopt interactivecomments` is on. **Run them from `infra/terraform/foundation`** unless a block
> starts with `cd`. No project ID, project number or email is committed: they come from `$(gcloud config get
> project)`, the gitignored `terraform.tfvars`, or a `google_project` data source.

> **Placeholders**
>
> | Placeholder | Meaning |
> |---|---|
> | `PROJECT_ID` | Your GCP project ID (`gcloud config get project`) |
> | `PROJECT_NUMBER` | The numeric project number |

**Contents**

1. [Concepts](#1-concepts)
2. [What the layer manages](#2-what-the-layer-manages)
3. [How the import was done](#3-how-the-import-was-done)
4. [Every diff, and what was done about it](#4-every-diff-and-what-was-done-about-it)
5. [Delete protection](#5-delete-protection)
6. [Secret values stay out of Terraform](#6-secret-values-stay-out-of-terraform)
7. [Removing Editor from the Compute Engine default SA](#7-removing-editor-from-the-compute-engine-default-sa)
8. [Verification checklist](#8-verification-checklist)
9. [Troubleshooting](#9-troubleshooting)
10. [Known gaps](#10-known-gaps)
11. [Lessons learned](#11-lessons-learned)
12. [Interview talking points](#12-interview-talking-points)
13. [ACE exam notes](#ace-exam-notes)

---

## 1. Concepts

### Import vs recreate

| | Recreate | **Import (chosen)** |
|---|---|---|
| Service account | New `unique_id`; old grants become `deleted:serviceAccount:…` | Same account, same ID |
| Secret | New container; values must be regenerated | Same secret, same versions |
| Repository | **Every image deleted** | Images untouched |
| In Google Cloud during the import | Deletions and creations | **Nothing changes**: Terraform only reads and records |

### `import` blocks vs `terraform import`

| | `terraform import ADDRESS ID` | **`import { to = …, id = … }`** (Terraform 1.5+) |
|---|---|---|
| When | Immediately, straight into state | During `plan` → `apply`, like any change |
| Preview | None | The plan shows `will be imported` and every diff first |
| Many at once | One command each | `for_each` (16 APIs, 3 secrets with one block each) |
| In the PR | No | Yes |

The `id` can use variables, so no project ID is written in the code. After the import is applied the blocks do
nothing; they were deleted, because on a rebuild from scratch there would be nothing to import.

### Why "no changes" after an import is the proof

A plan compares the code, the state and the real resource. Right after an import, the state *is* the real
resource, so **no changes = the code describes reality exactly**. Every `~` is a disagreement that applying would
"fix" by changing the real resource to match the code.

### Authoritative vs additive IAM

| Resource type | Owns | Grants it doesn't know about |
|---|---|---|
| `google_project_iam_policy` | The **whole** policy | **Removed**, including your Owner role: one apply can lock you out of the project |
| `google_project_iam_binding` | **Every member of one role** | Removed from that role (and any future grant of that role is removed at the next apply) |
| **`google_project_iam_member`** (used everywhere here) | **One member in one role** | Left alone |

The same three exist for service accounts, secrets and repositories (`google_secret_manager_secret_iam_member`, …).
The gcloud equivalents: `add-iam-policy-binding` / `remove-iam-policy-binding` are additive; **`set-iam-policy`**
replaces the whole policy.

---

## 2. What the layer manages

| File | Resources | Import ID shape |
|---|---|---|
| [`apis.tf`](../infra/terraform/foundation/apis.tf) | 16 × `google_project_service`, `disable_on_destroy = false` | `PROJECT_ID/SERVICE` |
| [`service_accounts.tf`](../infra/terraform/foundation/service_accounts.tf) | `exit-ticket-api`, `exit-ticket-nodes` | `projects/PROJECT_ID/serviceAccounts/EMAIL` |
| [`iam.tf`](../infra/terraform/foundation/iam.tf) | 3 project grants, 2 grants on the api SA, 2 secret grants, 1 repository grant | `RESOURCE ROLE MEMBER` |
| [`secrets.tf`](../infra/terraform/foundation/secrets.tf) | 3 secret **containers** (user-managed replication, us-east4) | `projects/PROJECT_ID/secrets/NAME` |
| [`artifact_registry.tf`](../infra/terraform/foundation/artifact_registry.tf) | `exit-ticket` repository: immutable tags, labels, cleanup policy in HCL | `projects/PROJECT_ID/locations/us-east4/repositories/exit-ticket` |
| [`outputs.tf`](../infra/terraform/foundation/outputs.tf) | `project_number`, `api_sa_email`, `nodes_sa_email`, `artifact_registry_repo`, `secret_ids` | — |

The IAM, exactly as Phase 4 documented:

| Member | Role | On |
|---|---|---|
| `exit-ticket-api` | `cloudsql.client`, `cloudsql.instanceUser` | Project |
| `exit-ticket-api` | `secretmanager.secretAccessor` | `session-secret`, `teacher-passcode` only |
| `PROJECT_ID.svc.id.goog[exit-ticket/api]` | `iam.workloadIdentityUser` | The `exit-ticket-api` SA |
| You (`admin_member` in `terraform.tfvars`) | `iam.serviceAccountTokenCreator` | The `exit-ticket-api` SA |
| `exit-ticket-nodes` | `container.defaultNodeServiceAccount` | Project |
| `exit-ticket-nodes` | `artifactregistry.reader` | The `exit-ticket` repository |
| `postgres-admin-password` | — | No bindings (only the project Owner reads it) |

**Why `disable_on_destroy = false`:** removing an API from the list, or destroying the layer, then only stops
Terraform managing it. Turning an API off breaks whatever uses it: other layers, the state bucket (`storage`),
Terraform itself (`serviceusage`, `cloudresourcemanager`). The 28 other enabled APIs (BigQuery's family,
`containerregistry`, `dns`, `oslogin`, …) were switched on automatically as dependencies and are left unmanaged.

**Why no `default_labels` here:** every resource was imported, and the import had to match reality. Labels are set
per resource, exactly as they were.

---

## 3. How the import was done

1. **Develop with local, empty state.** No `backend.tf` yet, `terraform init` locally, and iterate on `plan`.
   `plan` never writes state, so nothing could be imported by accident.
2. **One group at a time:** APIs → service accounts → secrets → repository and IAM, planning after each.
3. **Connect the backend** (prefix `foundation`); there was no local state, so nothing to migrate:
   ```bash
   terraform init -backend-config="bucket=$(gcloud config get project)-tfstate"
   terraform plan -out=tfplan
   ```
   `Plan: 30 to import, 0 to add, 3 to change, 0 to destroy.` The saved plan was checked with `terraform show -json
   tfplan`: the only changed fields were `labels` and `terraform_labels` (§4).
4. **Apply and prove it:**
   ```bash
   terraform apply tfplan
   rm tfplan
   terraform plan
   ```
   `30 imported, 0 added, 3 changed, 0 destroyed`, then **`No changes.`** State: 31 entries (30 + the
   `google_project` data source), serial 2, and **no `secret_data` anywhere**.

---

## 4. Every diff, and what was done about it

| Group | First plan | Cause | Done |
|---|---|---|---|
| APIs (16) | Clean | — | It also proved all 16 are enabled: importing something that doesn't exist fails the plan |
| Service accounts (2) | Clean | Display names and descriptions copied from the Phase 4 commands | — |
| Secrets (3) | `~ labels { + app }` on the two labelled secrets | **On import, the provider records `labels = {}`** (it can't know which existing labels Terraform should own) while `effective_labels` holds the real ones | **Accepted**: the apply rewrites the same value. Check that `effective_labels` matches the code, not that the diff is empty |
| Repository | `~ labels { + app, + env }` | The same | Accepted. All 3 cleanup policies, immutable tags and dry-run off matched the first time |
| IAM | **`7 to add`** | My mistake: one `for_each` import block aimed three items at **one** resource address. A `for_each` import can only target the instances of one `for_each` resource. `validate` didn't catch it | One `import` block per grant → `0 to add`. **`add` in an import plan means "not matched to anything real"**: applying would have tried to create grants that already exist |

---

## 5. Delete protection

A second apply, after the import, added protection that lives **only in state** (no API call; each update took 0 s):

| Resource | Setting | Why |
|---|---|---|
| Both service accounts | `deletion_policy = "PREVENT"` | A recreated SA gets a new `unique_id` and every grant to the old one breaks |
| The three secrets | `deletion_protection = true` | Deleting a secret destroys every version of its value |
| The repository | `deletion_policy = "PREVENT"` | Deleting it deletes every image the manifests pin by digest |

`Plan: 1 to import, 0 to add, 6 to change, 0 to destroy` (the 1 import is §7, step 1), each update one line, then
`No changes`. Like the state bucket in 5A: with `PREVENT` in state, a plan that would delete the resource fails, even
if its block is deleted from the code.

---

## 6. Secret values stay out of Terraform

There is no `google_secret_manager_secret_version` in the code, and there never will be. A version's `secret_data`
would be written to the state **in plain text**, readable by anyone who can read the state bucket. `sensitive`
only hides it in terminal output.

So Terraform owns the **container** (name, replication, labels, IAM) and the values arrive through pipes, as in
Phase 4. [`scripts/create-app-secrets.sh`](../scripts/create-app-secrets.sh) changed accordingly: it used to
`gcloud secrets create` (container + version 1); it now requires the container to exist (from Terraform) and to
have **no** version yet, then runs `gcloud secrets versions add … --data-file=-`. On a rebuild from scratch:

```bash
cd ~/projects/exit-ticket-5th-grade-math-science/infra/terraform/foundation
terraform apply
cd ~/projects/exit-ticket-5th-grade-math-science
scripts/create-app-secrets.sh
openssl rand -base64 32 | tr -d '\n' | gcloud secrets versions add postgres-admin-password --data-file=-
```

(The script wasn't run in 5B: the versions already exist. `bash -n` checks its syntax.)

---

## 7. Removing Editor from the Compute Engine default SA

`PROJECT_NUMBER-compute@developer.gserviceaccount.com` got **Editor** automatically when the Compute Engine API was
enabled. Editor can change almost anything in the project, **including reading the state bucket**. Since Phase 4
nothing of ours runs as it.

### Checking dependents first (all read-only)

```bash
PN="$(gcloud projects describe $(gcloud config get project) --format='value(projectNumber)')"
gcloud projects get-iam-policy $(gcloud config get project) --flatten="bindings[].members" --filter="bindings.role=roles/editor" --format="table(bindings.members)"
gcloud services list --enabled --format="value(config.name)" | sort
gcloud compute instances list --format="table(name,serviceAccounts[].email.list())"
gcloud builds list --limit=5 --format="table(id,createTime,status,serviceAccount)"
gcloud iam service-accounts keys list --iam-account="${PN}-compute@developer.gserviceaccount.com" --managed-by=user
gcloud iam service-accounts get-iam-policy "${PN}-compute@developer.gserviceaccount.com"
gcloud logging read "protoPayload.authenticationInfo.principalEmail=\"${PN}-compute@developer.gserviceaccount.com\"" --freshness=30d --limit=20 --format="table(timestamp,protoPayload.serviceName,protoPayload.methodName)"
```

| Check | Found |
|---|---|
| Who has Editor | Only the compute SA (no Google APIs Service Agent in this project) |
| Services that could run as it | Cloud Run, Cloud Functions, App Engine, Dataflow: **APIs off**. No VMs, no clusters, no Cloud Build runs |
| Keys / who can act as it | None / no bindings |
| Its API calls, 30 days | None (Admin Activity log) |

Limits: Data Access logs are off, so read-only use wouldn't show; Activity Analyzer and the IAM Recommender need APIs
that aren't enabled (gcloud offers to enable them: answer `N`). The remaining risk is a future default: Phase 7's
Cloud Build gets its own service account instead.

### The options

| Option | Verdict |
|---|---|
| **A. Import the grant as `google_project_iam_member`, then delete the block** | ✅ **Chosen.** The plan shows exactly one member removed from one role |
| B. `google_project_default_service_accounts`, `action = "DEPRIVILEGE"` | One apply, but the plan only says `+ create`: it doesn't show which grant disappears |
| C. `google_project_iam_binding` for `roles/editor`, no members | ❌ Authoritative: Terraform would own the role and strip any future Editor grant |
| D. `gcloud projects remove-iam-policy-binding` | Works, but not in code |

### The two applies

1. Import, no change: `import { to = google_project_iam_member.compute_default_editor, id = "PROJECT_ID roles/editor
   serviceAccount:PROJECT_NUMBER-compute@developer.gserviceaccount.com" }` plus a matching resource block (the
   project number comes from `data.google_project`).
2. Delete the block and the import:
   ```
   # google_project_iam_member.compute_default_editor will be destroyed
   # (because google_project_iam_member.compute_default_editor is not in configuration)
   Plan: 0 to add, 0 to change, 1 to destroy.
   ```
   `Destruction complete after 8s`. "Destroy" on an `_iam_member` removes one member from one role; the service
   account itself is untouched.

Verified from Google's side: the compute SA has **no** project roles; our two SAs keep exactly their three grants;
`terraform plan` → `No changes`.

Rollback, if something ever fails with `403 PERMISSION_DENIED` as that SA:
```bash
gcloud projects add-iam-policy-binding $(gcloud config get project) --member="serviceAccount:$(gcloud projects describe $(gcloud config get project) --format='value(projectNumber)')-compute@developer.gserviceaccount.com" --role="roles/editor" --condition=None
```
Better: give that workload its own least-privilege service account.

---

## 8. Verification checklist

| Check | Command | Healthy |
|---|---|---|
| Code matches reality | `terraform plan` | `No changes.` |
| Managed resources | `terraform state list \| wc -l` | `31` (30 resources + the `google_project` data source) |
| No secret values in state | `terraform state pull \| grep -c secret_data` | `0` |
| Default SA has no roles | `gcloud projects get-iam-policy $(gcloud config get project) --flatten="bindings[].members" --filter="bindings.members:compute@developer.gserviceaccount.com" --format="table(bindings.role)"` | No rows |
| Our SAs' project roles | `gcloud projects get-iam-policy $(gcloud config get project) --flatten="bindings[].members" --filter="bindings.members:serviceAccount:exit-ticket-" --format="table(bindings.role,bindings.members)"` | `cloudsql.client`, `cloudsql.instanceUser` (api); `container.defaultNodeServiceAccount` (nodes) |
| Secret readers | `gcloud secrets get-iam-policy session-secret --format="value(bindings.members)"` (and `teacher-passcode`) | Only `exit-ticket-api` |
| No keys | `gcloud iam service-accounts keys list --iam-account=exit-ticket-api@$(gcloud config get project).iam.gserviceaccount.com --managed-by=user` (and nodes) | `Listed 0 items.` |
| Protections in state | `terraform state show 'google_service_account.api' \| grep deletion_policy` | `"PREVENT"` |

Verified 2026-11-11.

---

## 9. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `N to add` in an import plan | An import block doesn't match the resource address (or is missing) | Fix the `to`; one block per resource unless it's a `for_each` resource |
| `Cannot import non-existent remote object` | Wrong ID shape, or the thing doesn't exist (e.g. a disabled API) | Check the provider docs' import section; `gcloud … describe` |
| `~ labels { + … }` right after import | Imported resources start with `labels = {}` | Expected; compare `effective_labels` |
| `deletion_policy is PREVENT` / `deletion_protection` error on a plan | The protections (§5) | Intended. Change the setting in code and apply first if a deletion is really meant |
| `create-app-secrets.sh`: "doesn't exist. Create it with Terraform first" | Fresh project, foundation not applied | `terraform apply` in `foundation/` |
| `create-app-secrets.sh`: "already has a version" | Values exist | Rotate with `gcloud secrets versions add` instead |
| gcloud asks to enable an API during a read-only check | The command belongs to a disabled service | Answer `N`; a disabled API is itself the answer |
| `Error acquiring the state lock` | Another run, or a crashed one | See [5A §8](phase-5a-bootstrap.md#8-troubleshooting) |

---

## 10. Known gaps

| Gap | Why it's acceptable now | Later |
|---|---|---|
| The Owner account (you) can read the state bucket and every secret | One-person project | Phase 7: Cloud Build gets its own SA with access to the bucket only |
| Labels differ between resources (secrets `app`; repository `app`, `env`; bootstrap adds `managed-by`) | The import had to match reality | A deliberate labelling change later |
| The 28 dependency APIs are unmanaged | Google enables them as needed | — |
| One identity for api and migrate | Unchanged from Phase 4 | [Phase 4 §8](phase-4-cloud-sql.md#8-known-gaps) |
| Network (custom VPC, private services access) not built yet | Part 2 of the foundation | Phase 5C |

---

## 11. Lessons learned

| Session | What happened | Lesson |
|---|---|---|
| 5B | Plan said `7 to add` for IAM that already existed | A `for_each` import targets one `for_each` resource; `validate` doesn't catch it. In an import plan, `add` means "not matched" |
| 5B | Imported secrets and repository showed label "updates" | Imports start with `labels = {}`; check `effective_labels`, not an empty diff |
| 5B | Expected a Google service agent with Editor; there was none | Check the real policy before reasoning about it; Google's defaults change over time |
| 5B | A gcloud `table` format printed nothing for an empty list, so outputs ran together | Label each command's output (`echo "--- name"`) when pasting several |
| 5B | Several command blocks across messages; steps got out of order | One step per message, wait for its output |
| 5B | The default SA's Editor could read the state bucket | Remove broad roles before state holds anything sensitive |

---

## 12. Interview talking points

**1. "I brought hand-built infrastructure under Terraform without recreating anything."** 30 resources imported with
`import` blocks; every diff explained; the first plan after the import said `No changes`.

**2. "Additive IAM only."** Every grant is an `_iam_member`, so Terraform never removes access it doesn't know about,
and it can't lock anyone out of the project.

**3. "I closed the Editor gap safely."** Checked services, VMs, builds, keys, impersonation rights and 30 days of
audit logs; then imported the one grant and deleted it, so the plan showed exactly one removal, with a one-line
rollback ready.

**4. "Secrets never touch state."** Terraform manages the containers and access; values are piped straight into
Secret Manager.

---

## ACE exam notes

**Roles**
- **Basic** roles (Owner, Editor, Viewer) apply to almost every service in the project: too broad for workloads.
  **Predefined** roles are per service (`roles/cloudsql.client`). **Custom** roles bundle chosen permissions (project or
  organization level; not every permission is supported).
- A role is a set of **permissions** (`service.resource.verb`, e.g. `secretmanager.versions.access`).
- **Allow policies** bind members to roles on a resource; they're **inherited** downward (organization → folder →
  project → resource) and **combined** (the union). A lower level can't take away what a higher level grants; **deny
  policies** can.
- **IAM Conditions** add attribute rules (resource name, time) to a binding.
- `add-iam-policy-binding` / `remove-iam-policy-binding` are additive; `set-iam-policy` replaces the whole policy (the
  `etag` stops two writers overwriting each other).

**Service accounts**
- A service account is both an **identity** (it can be granted roles) and a **resource** (others can be granted roles
  *on* it).
- On the SA: **`iam.serviceAccountUser`** = attach it to a resource (actAs); **`iam.serviceAccountTokenCreator`** = mint
  tokens / impersonate it; **`iam.workloadIdentityUser`** = let a Kubernetes SA act as it.
- **Default service accounts** (Compute Engine `PROJECT_NUMBER-compute@developer…`, App Engine `PROJECT_ID@appspot…`)
  historically got **Editor**; best practice is to remove it and use dedicated SAs. The organization policy
  `iam.automaticIamGrantsForDefaultServiceAccounts` stops the automatic grant.
- **Service agents** (`service-PROJECT_NUMBER@gcp-sa-…`) are Google-managed identities a service uses on your behalf;
  don't remove their roles.
- Prefer **short-lived credentials** (Workload Identity, impersonation, the metadata server) over **keys**; disable key
  creation with `iam.disableServiceAccountKeyCreation`. List keys with `gcloud iam service-accounts keys list
  --managed-by=user`.
- Deleting a SA and recreating it with the same name gives a **new unique ID**: old grants don't apply.
  `gcloud iam service-accounts undelete` works for 30 days. **Disable** before deleting.

**Finding unused access**
- **IAM Recommender** (role recommendations from 90 days of usage), **Policy Analyzer** ("who can access what"),
  **Activity Analyzer** (when a SA last authenticated), and **Cloud Audit Logs** (Admin Activity always on; Data
  Access off by default).

**APIs**
- Enabled per project; `gcloud services enable|disable|list --enabled`. Disabling an API can break resources that
  depend on it.
