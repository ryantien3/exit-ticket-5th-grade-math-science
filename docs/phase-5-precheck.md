# Phase 5 — Pre-check: a known-good start for Terraform

Before writing any Terraform, we checked the repo, the local tools, credentials, every cloud resource built by hand
in Phases 1–4, and the app itself, so Phase 5 starts from a verified state. This doc records what was found
(2026-11-11) and the decisions to make before the first `.tf` file.

| Part | What | PR |
|---|---|---|
| **Pre-check** | Repo, tools, gcloud and ADC, APIs, cloud inventory and cost, local app; decisions (a)–(e) | (this PR) |
| `.gitignore` fix | Terraform plans, JSON tfvars, overrides, CLI config | #19 |

> **Every command in this doc is 📖 REFERENCE.** They were run during the pre-check (read-only) or are shown for a
> later session. No code block contains `#` comments (zsh treats `#` as an argument).

> **Placeholders**
>
> | Placeholder | Meaning |
> |---|---|
> | `PROJECT_ID` | Your GCP project ID (`gcloud config get project`) |
> | `PROJECT_NUMBER` | The numeric project number |

**Contents**

1. [Results](#1-results)
2. [Things to fix or watch](#2-things-to-fix-or-watch)
3. [What Terraform will take over](#3-what-terraform-will-take-over)
4. [Decisions before writing Terraform](#4-decisions-before-writing-terraform)
5. [Verdict](#5-verdict)
6. [ACE exam notes](#ace-exam-notes)

---

## 1. Results

✅ healthy · ⚠️ works, but note or decide · ❌ broken

| # | Check | Status | Found |
|---|---|---|---|
| 1.1 | Git: tree, branch, sync | ✅ | Clean `main`, equal to `origin/main`; no other branches; no open PRs |
| 1.2 | `.gitignore` for Terraform | ✅ | Was missing `*.tfvars.json`, `*.tfplan`, override files, `.terraformrc`. **Fixed in #19**, verified with `git check-ignore` on 17 names. `.terraform.lock.hcl` stays tracked |
| 1.3 | `infra/` | ✅ | `artifact-registry/cleanup-policy.json` and an empty `terraform/` (`.gitkeep`) |
| 2 | Local tools | ✅ | terraform **1.17.2** (upgraded from 1.16.6, `hashicorp/tap`), gcloud SDK 590.0.0, kubectl 1.35.9, Docker 29.8.2 + Compose v5.6.0 + buildx 0.37.2 (amd64 builds), cloud-sql-proxy 2.26.3, gke-gcloud-auth-plugin 0.5.20 |
| 3.1 | gcloud configuration | ✅ | `exit-ticket` active: project `PROJECT_ID`, `us-east4`, `us-east4-a`. (The inactive `default` configuration points at `us-east1`) |
| 3.2 | Application Default Credentials | ✅ | `GOOGLE_APPLICATION_CREDENTIALS` unset; ADC is `authorized_user` (your account, not a key), quota project set, token valid |
| 3.3 | APIs | ✅ | All enabled: compute, servicenetworking, cloudresourcemanager, iam, iamcredentials, storage, container, sqladmin, secretmanager, artifactregistry, serviceusage |
| 4.1 | Nothing billing unexpectedly | ✅ | No clusters, forwarding rules, backend services, NEGs, disks, addresses, instances or routers; no local proxy |
| 4.2 | Cloud SQL `exit-ticket-db` | ⚠️ | `STOPPED`, `NEVER`, deletion protection on, public IP only, no backups. Past the 7-day delete deadline (2026-11-04): **kept on purpose** for import ([§4a](#a-import-existing-resources-or-rebuild-clean)) |
| 4.3 | Service accounts and keys | ✅ | `exit-ticket-api`, `exit-ticket-nodes`, Compute Engine default; **0 user-managed keys** |
| 4.4 | Project IAM | ⚠️ | api: `cloudsql.client`, `cloudsql.instanceUser`; nodes: `container.defaultNodeServiceAccount`; **Compute Engine default SA: `roles/editor`** ([§4e](#e-removing-editor-from-the-compute-engine-default-sa)) |
| 4.5 | IAM on the SAs | ✅ | api: `workloadIdentityUser` (`PROJECT_ID.svc.id.goog[exit-ticket/api]`), `serviceAccountTokenCreator` (you); nodes: none |
| 4.6 | Secrets | ✅ | 3 secrets in `us-east4`, version 1 enabled each; accessor only `exit-ticket-api` on two; `postgres-admin-password` has no bindings |
| 4.7 | Artifact Registry | ✅ | `exit-ticket`, immutable tags, 96.75 MB; cleanup policy identical to the JSON file, dry run off; `exit-ticket-nodes` is the only reader |
| 4.8 | Network | ⚠️ | Only `default` (auto mode, us-east4 = `10.150.0.0/20`); four `default-allow-*` rules, **SSH and RDP open to `0.0.0.0/0`**; no VPC peering, no reserved ranges |
| 4.9 | Budget | ✅ | $25 per calendar month, credits excluded, alerts at 50/90/100% actual + 100% forecasted, default email recipients. Had no project filter (it covered the whole billing account); **filter added 2026-11-11**, see [§2](#2-things-to-fix-or-watch) |
| 5.1 | pytest | ✅ | 91 passed (one harmless Starlette deprecation warning) |
| 5.2 | Full-container mode | ✅ | Built and up; `alembic upgrade head` as a one-off container → `0002 (head)`; `/`, `/teacher.html`, `/api/healthz`, `/api/readyz`, `/api/standards` all 200; stopped afterwards |
| 5.3 | Images for GKE | ✅ | Both `linux/amd64` (`uname -m` = `x86_64` in the api), api runs as `uid=10001` |

---

## 2. Things to fix or watch

Nothing blocks Phase 5. These are open items, in rough order of when they matter:

| Item | Action | When |
|---|---|---|
| ~~Budget scope~~ | **Done 2026-11-11.** The account pays only for this project, but a project filter was added anyway so alerts always mean "exit-ticket". `creditTypesTreatment` stayed `EXCLUDE_ALL_CREDITS` | — |
| No Cloud SQL backup has ever been taken | Start the instance and run an on-demand backup (`gcloud sql backups create --instance=exit-ticket-db`) **before** Terraform changes it for the first time | The session that imports Cloud SQL |
| Cloud SQL kept stopped past the deadline | Accepted: ~$2–4/month while it waits to be imported. Still stopped at the end of every session | Ongoing |
| Default network's SSH/RDP rules open to the internet | Nothing listens on them today; settled by decision (c) | With the VPC |
| Compute Engine default SA has Editor | Decision (e) | After the Terraform cluster works |
| `servicenetworking` is already on; [phase-4-design §4](phase-4-design.md#4-connecting-auth-proxy-network-database-auth) says it wasn't | No action: no peering or range exists, so private services access was never set up | — |
| Outdated brew formulae; macOS 14 is a Homebrew Tier 3 configuration | Upgrade in a quiet moment, not mid-phase (Python upgrades can disturb the `.venv`) | Any time |

---

## 3. What Terraform will take over

Everything below exists today, made by hand. Import ID shapes come from the Google provider docs; recheck them on
the day.

| Resource | Terraform type | Import ID shape |
|---|---|---|
| `exit-ticket-api`, `exit-ticket-nodes` | `google_service_account` | `projects/PROJECT_ID/serviceAccounts/EMAIL` |
| Project roles on them | `google_project_iam_member` | `PROJECT_ID ROLE MEMBER` |
| `workloadIdentityUser`, `serviceAccountTokenCreator` on `exit-ticket-api` | `google_service_account_iam_member` | `projects/PROJECT_ID/serviceAccounts/EMAIL ROLE MEMBER` |
| 3 secrets (containers only) | `google_secret_manager_secret` | `projects/PROJECT_ID/secrets/NAME` |
| Accessor bindings on 2 secrets | `google_secret_manager_secret_iam_member` | `projects/PROJECT_ID/secrets/NAME ROLE MEMBER` |
| Repository + cleanup policy | `google_artifact_registry_repository` | `projects/PROJECT_ID/locations/us-east4/repositories/exit-ticket` |
| Node SA reader binding | `google_artifact_registry_repository_iam_member` | `projects/PROJECT_ID/locations/us-east4/repositories/exit-ticket ROLE MEMBER` |
| `exit-ticket-db` | `google_sql_database_instance` | `projects/PROJECT_ID/instances/exit-ticket-db` |
| Database `exitticket` | `google_sql_database` | `projects/PROJECT_ID/instances/exit-ticket-db/databases/exitticket` |
| IAM database user | `google_sql_user` | `PROJECT_ID/exit-ticket-db/USER_NAME` |
| APIs | `google_project_service` | `PROJECT_ID/SERVICE` |

**New, created by Terraform:** the state bucket, the VPC and subnet, the private services access range and peering,
the GKE Autopilot cluster.

**Deliberately outside Terraform:**
- **Secret values (versions).** Anything Terraform manages is written to the state file in plain text, so a
  `google_secret_manager_secret_version` would put the secret into state. Values keep coming from
  `scripts/create-app-secrets.sh` and pipes.
- **The `postgres` password**, for the same reason.
- **The two in-database `GRANT`s** (`CONNECT`, `USAGE, CREATE ON SCHEMA public`). They live inside Postgres, which
  the Google provider doesn't manage. They survive because the instance is imported, not rebuilt.
- **Kubernetes manifests and images.** Still `kubectl apply` and `scripts/push-images.sh`; Cloud Build takes over in
  Phase 7.

---

## 4. Decisions before writing Terraform

### (a) Import existing resources, or rebuild clean

**Import** means writing Terraform code that describes a resource exactly as it already is, then telling Terraform
"this real thing is that block" (an `import` block, Terraform 1.5+). The first `plan` should then say **No
changes**. **Rebuild** means deleting the hand-made resource and letting Terraform create a new one.

| | Import | Rebuild |
|---|---|---|
| Pros | Nothing is deleted: images, secret values, IAM grants, in-database GRANTs and the IP stay. Practises the real-world skill (most companies adopt Terraform on top of existing infrastructure) | Simpler code (no import blocks); proves the code can build everything from zero |
| Cons | Every setting must match or `plan` shows a diff; a few settings force replacement if you get them wrong; slower to write | Deleting a service account changes its unique ID and leaves `deleted:serviceAccount:…` bindings behind; recreating a secret means new values; deleting the repository deletes every image; the GRANTs have to be redone |

**Decided:** import, including `exit-ticket-db` (decided 2026-11-11; the user accepts ~$2–4/month while it waits).
Order: free resources first (service accounts, IAM, secrets, repository), Cloud SQL last.

Gotchas to expect with Cloud SQL:
- **`activation_policy`** is a Terraform setting. Our instance is `NEVER` between sessions. If the code said
  `ALWAYS`, the next `apply` would **start** it. Write `NEVER` or tell Terraform to ignore that field
  (`lifecycle { ignore_changes }`).
- **Two deletion protections.** `deletion_protection` (Terraform refuses to destroy) and
  `settings.deletion_protection_enabled` (the API refuses to delete). Keep both on.
- **Plan before apply, every time.** A `plan` that says `must be replaced` (`-/+`) on the instance means destroy and
  recreate: stop and fix the code.

ACE: Setting up a cloud solution environment and Planning and implementing a cloud solution (infrastructure as
code: Terraform, Infrastructure Manager). The exam won't ask about `import` syntax; it will ask why you'd manage
infrastructure as code.

### (b) The state bucket, and the chicken-and-egg problem

Terraform keeps **state**: a JSON file mapping each block in your code to a real resource ID, with every attribute.
Kept in a **GCS bucket**, it's shared (your Mac and Cloud Build in Phase 7), **locked** while one `apply` runs (the
GCS backend locks natively, no extra database), and never in git (it can contain secrets).

The problem: the bucket holding the state should be managed by Terraform too, but Terraform needs the bucket
before it can store any state.

| Option | How | Pros | Cons |
|---|---|---|---|
| 1. Create it by hand | `gcloud storage buckets create` once, documented in the runbook | Simplest; the exam-style command | One resource not described in code |
| 2. **Bootstrap config (recommended)** | A tiny separate config (`infra/terraform/bootstrap`) creates the bucket with **local** state, then `terraform init -migrate-state` moves that state into the bucket itself, under its own prefix | Everything in code; teaches backends and state migration | One extra folder and step; a `prevent_destroy` is essential so the bucket never deletes its own state |
| 3. Infrastructure Manager | Google runs Terraform for you and stores the state | No bucket to manage | Hides exactly what we want to learn; a different workflow from Phase 7's Cloud Build |

Proposed settings:

| Setting | Proposal | Why |
|---|---|---|
| Name | `PROJECT_ID-tfstate` | Bucket names are **globally unique**; the project ID makes collisions unlikely. Names are visible to anyone who guesses them, so nothing secret in a name |
| Location | `us-east4`, regional | Same region as everything else; state is a few KB, so multi-region buys nothing. (Cloud Storage's Always Free tier covers only us-east1, us-west1 and us-central1, but a few KB costs effectively $0 anyway) |
| Class | Standard | Read on every `plan`; Nearline/Coldline charge per read and have minimum storage durations |
| Versioning | **On**, with a lifecycle rule keeping ~10 old versions | Every `apply` writes a new version; a bad or corrupted state can be rolled back |
| Access | Uniform bucket-level access, public access prevention **enforced** | IAM only (no per-object ACLs); can never be made public |
| Prefix | `env/dev` for the main config, `bootstrap` for the bucket's own state | One bucket, separate state files |

One quirk: the `backend "gcs"` block **can't use variables**; the bucket name is written literally or passed with
`terraform init -backend-config=…`.

ACE: Planning and implementing a cloud solution (Cloud Storage location types, storage classes, versioning,
lifecycle rules, uniform bucket-level access).

### (c) A new custom VPC, or the default network

The `default` network is **auto mode**: Google made one subnet in **every** region with fixed ranges inside
`10.128.0.0/9`, and opened SSH, RDP and ICMP to the internet. A **custom mode** network has only the subnets you
create and **no** allow rules: ingress is denied and egress allowed (the two implied rules) until you add rules.

| | New custom VPC (recommended) | Keep using `default` |
|---|---|---|
| Pros | You choose every range (no overlap surprises when peering); no open SSH/RDP; everything about the network is in code; matches production practice and what interviewers expect | Already exists; nothing to design |
| Cons | Ranges to plan; the GKE cluster and Cloud SQL's private IP must be pointed at it | Can't be adopted cleanly by Terraform (auto mode, ~40 subnets); open firewall rules; ranges you didn't choose |

Draft IP plan (all outside `10.128.0.0/9`, so nothing overlaps `default`):

| Range | CIDR | Used by |
|---|---|---|
| Subnet `exit-ticket-us-east4` | `10.10.0.0/20` | GKE nodes |
| Secondary `pods` | `10.20.0.0/17` | Pod IPs (Autopilot's default pod range size) |
| Secondary `services` | `10.30.0.0/22` | Kubernetes Service IPs |
| Private services access | `10.40.0.0/20` | Cloud SQL's private IP (decision d) |

Two choices that come with it:
- **Private Google Access on the subnet: on.** Lets nodes without public IPs reach Google APIs (Artifact Registry,
  Secret Manager, Cloud SQL Admin, Logging).
- **Cloud NAT: probably not needed.** NAT is only for reaching the *public internet* from private nodes, and bills
  per hour plus per GB. Our images come from Artifact Registry and the proxy image from Google's registry, both
  reachable through Private Google Access. To be confirmed when the cluster is designed.

What to do with `default`: leave it untouched while nothing uses it; delete its SSH/RDP rules (or the network) once
everything is on the new VPC.

ACE: Planning and implementing a cloud solution (VPC: auto vs custom mode, subnets, secondary ranges, firewall rules
and implied rules, Private Google Access, Cloud NAT).

### (d) Cloud SQL private IP through private services access

Cloud SQL doesn't run in your VPC: it runs in a Google-owned network. **Private services access** connects the two:
1. Reserve an internal range in your VPC for Google services (`google_compute_global_address`, purpose
   `VPC_PEERING`, the `10.40.0.0/20` above).
2. Create the **peering** between your VPC and Google's (`google_service_networking_connection`; this is what the
   `servicenetworking` API is for).
3. Give the instance a private IP from that range (`settings.ip_configuration.private_network`).

GKE pods then reach the database at an internal IP; the proxy in the sidecar uses `--private-ip`, still with IAM
login and TLS.

**What it means for your Mac:** your Mac is not in the VPC, and peering is not a VPN. **A private IP is unreachable
from the Mac.** Today's local proxy (seeding, the one-time GRANTs) uses the public IP.

| Option | How | Pros | Cons |
|---|---|---|---|
| 1. **Both IPs for now (recommended first step)** | Add the private IP, keep the public one (no authorized networks, connector enforcement). Pods use `--private-ip`; the Mac proxy keeps working | Nothing breaks; small, reviewable change | Public endpoint remains (still unreachable without IAM + the proxy); IP still billed |
| 2. Private only, admin from inside | Turn off public IP (`ipv4_enabled = false`); seed and admin as Kubernetes Jobs, like the migrate Job | The real production shape; removes the public IP charge | Needs the cluster running for any database task |
| 3. Private only, bastion | A small VM in the VPC, reached with `gcloud compute start-iap-tunnel` (IAP TCP forwarding) | Mac access with no public database IP; strong ACE topic | A VM to create, secure and pay for while it runs |

Facts to plan around (recheck in the docs on the day):
- **Private IP can't be turned off** once an instance has it. Moving the instance to a different network later
  isn't a simple switch either.
- Adding a private IP to an existing instance **restarts** it, and the instance must be running for the change.
- The peering **can't be deleted while an instance uses it**, and peering is **not transitive**: another network
  peered to ours can't reach Cloud SQL through it.
- Private Service Connect is the newer alternative (an endpoint in your subnet instead of peering); private
  services access is what the ACE exam and most docs use.

ACE: Planning and implementing a cloud solution (Cloud SQL connectivity, VPC peering, private services access) and
Configuring access and security (IAP TCP forwarding).

### (e) Removing Editor from the Compute Engine default SA

`PROJECT_NUMBER-compute@developer.gserviceaccount.com` was created with the API and given **Editor**: a basic role
that can change almost everything in the project. Since Phase 4 our nodes don't use it. But other services quietly
default to it: **new VMs**, **Cloud Run** and **Cloud Functions**, and, in projects created since 2024, **Cloud
Build's default service account**, which matters for Phase 7.

Check dependents first (all read-only):
```bash
gcloud logging read 'protoPayload.authenticationInfo.principalEmail:"compute@developer.gserviceaccount.com"' --freshness=30d --limit=20 --format="table(timestamp,protoPayload.serviceName,protoPayload.methodName)"
gcloud policy-intelligence query-activity --activity-type=serviceAccountLastAuthentication --project=$(gcloud config get project)
gcloud recommender recommendations list --recommender=google.iam.policy.Recommender --location=global --project=$(gcloud config get project) --format="table(description,stateInfo.state)"
gcloud compute instances list
gcloud run services list
gcloud functions list
```
(Audit log entries where it acted; when it last authenticated, from Activity Analyzer; the IAM Recommender's
suggestions, which need weeks of history and may be empty for a young project; anything attached to it.)

| When | Pros | Cons |
|---|---|---|
| Now, by hand | Closes the gap immediately | Not in code; a rebuild or new API enable could surprise us |
| **After the Terraform cluster works, through Terraform (recommended)** | Import the binding, then delete it from the code: `plan` shows exactly one removal, reviewed and recorded | The gap stays open a little longer |
| In Phase 8 (IAM review) | Fits the least-privilege review | Leaves Editor around while Cloud Build is set up in Phase 7 |

Rollback if something breaks: re-add the binding (`gcloud projects add-iam-policy-binding … --role=roles/editor`),
then give that service its own least-privilege SA. **Disable** the account rather than delete it if we ever go
further: a deleted default SA is hard to get back.

ACE: Configuring access and security (basic vs predefined roles, default service accounts, least privilege, IAM
Recommender, Policy Analyzer and Activity Analyzer, audit logs).

### Also on the list (later in Phase 5)

- **Per-session teardown with Terraform.** The strict teardown policy deletes the cluster every session. The
  cluster should be easy to destroy and recreate without touching the network or the database: a separate state
  or a `cluster_enabled` variable. To be designed with the cluster.
- **APIs in code.** `google_project_service` for each API, with `disable_on_destroy = false` so a `destroy` never
  turns off an API other things use.
- **IAM resource type.** Use `google_*_iam_member` (adds one member, leaves the rest alone). Never `_policy` (it
  replaces the whole policy and can lock you out of the project); `_binding` owns every member of a role.

---

## 5. Verdict

**Ready to start Phase 5.** Every check passed or has a recorded decision.

- **Decisions confirmed 2026-11-11:** (a) import, including `exit-ticket-db`; (b)–(e) as recommended: bootstrap config for the state bucket, a new custom VPC, private IP alongside the public one first, Editor removed through Terraform once the new cluster works.
- Budget project filter added.
- Work continues on branch `infra/phase-5-terraform`.

---

## ACE exam notes

- **Credentials:** `gcloud auth login` is for the CLI; **Application Default Credentials** are for code and tools
  (Terraform, client libraries). ADC looks at `GOOGLE_APPLICATION_CREDENTIALS`, then the user ADC file, then the
  metadata server. Set a quota project for user ADC.
- **APIs** are enabled per project (`gcloud services enable`); a missing API is a classic deployment failure.
- **Budgets** belong to the **billing account** and are scoped by filters; they **alert but never cap spending**
  (capping needs Pub/Sub + automation).
- **Terraform** is Google's recommended IaC tool; **Infrastructure Manager** runs it as a managed service;
  Deployment Manager is deprecated. State goes in a GCS bucket with versioning.
- **VPC:** auto vs custom mode; implied rules (deny ingress, allow egress); firewall priority (lower number wins);
  Private Google Access; Cloud NAT for internet egress from private IPs.
- **Cloud SQL private IP:** private services access = reserved range + VPC peering with `servicenetworking`; peering
  is non-transitive; reach a private instance from outside with a bastion and IAP, or from inside the VPC.
- **IAM:** basic roles (Owner, Editor, Viewer) are too broad for workloads; default service accounts get Editor;
  use dedicated SAs with predefined roles; Recommender suggests removing unused permissions.
