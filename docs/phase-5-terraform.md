# Phase 5 — Terraform: the runbook

Phase 5 turned everything built by hand in Phases 1–4 into Terraform, without recreating anything that held data or
identity: 34 resources were **imported** (30 + the Editor grant in foundation, imported only to be removed; 3 in data), the network was built new, and the GKE cluster
became a layer that is created and destroyed every session. Along the way the remaining Phase 4 security gaps were
closed: Editor is gone from the default service account, Cloud SQL has a private IP, and the cluster has private nodes
and no public control plane endpoint. This document is the runbook for the whole phase: how the layers fit, what
they cost, how to build from scratch, run a session, tear it down, and check for drift. The part docs hold the full
story of each session.

| Part | What | Doc | PR |
|---|---|---|---|
| **Pre-check** | Known-good start; decisions (a)–(e) | [phase-5-precheck.md](phase-5-precheck.md) | #19, #20 |
| **5A** | Layer layout; state bucket; its state migrated into itself | [phase-5a-bootstrap.md](phase-5a-bootstrap.md) | #21 |
| **5B** | Foundation part 1: 30 imports, delete protections, Editor removed | [phase-5b-foundation.md](phase-5b-foundation.md) | #22 |
| **5C** | Foundation part 2: custom VPC, secondary ranges, private services access | [phase-5c-network.md](phase-5c-network.md) | #23 |
| **5D** | `data` layer: backup, Cloud SQL imported, private IP | [phase-5d-cloud-sql.md](phase-5d-cloud-sql.md) | #25 |
| **5E** | `cluster` layer: private Autopilot, DNS endpoint, `--private-ip`, drills | [phase-5e-cluster.md](phase-5e-cluster.md) | #26 |
| **5F** | This runbook, drift check, repo audit | (this doc) | (this PR) |

> **About the commands in this doc.** Code blocks contain commands only, no `#` comments: in zsh, `#` is not a
> comment unless `setopt interactivecomments` is on. **Run them from the repo root** unless a block starts with `cd`.
> No project ID is committed: it comes from `$(gcloud config get project)` or each layer's gitignored
> `terraform.tfvars`.

> **Placeholders**
>
> | Placeholder | Meaning |
> |---|---|
> | `PROJECT_ID` | Your GCP project ID (`gcloud config get project`) |
> | `PROJECT_NUMBER` | The numeric project number |

**Contents**

1. [The layers](#1-the-layers)
2. [What exists, and what it costs](#2-what-exists-and-what-it-costs)
3. [Build from scratch](#3-build-from-scratch)
4. [Starting a session](#4-starting-a-session)
5. [Ending a session](#5-ending-a-session)
6. [Verification checklist](#6-verification-checklist)
7. [Drift](#7-drift)
8. [Troubleshooting](#8-troubleshooting)
9. [End-of-project teardown](#9-end-of-project-teardown)
10. [Known gaps](#10-known-gaps)
11. [Lessons learned](#11-lessons-learned)
12. [Interview talking points](#12-interview-talking-points)
13. [ACE exam notes](#ace-exam-notes)

---

## 1. The layers

```
 GCS bucket PROJECT_ID-tfstate  (versioned, uniform access, public access prevention, 3 delete locks)
 ├── bootstrap/default.tfstate   ◄── bootstrap/    the bucket itself                         forever
 ├── foundation/default.tfstate  ◄── foundation/   APIs, SAs, IAM, secret containers,        months, ~$0
 │                                                 Artifact Registry, VPC, subnet, PSA
 ├── data/default.tfstate        ◄── data/         Cloud SQL instance, database, IAM user    months, stopped
 ├── cluster/default.tfstate     ◄── cluster/      GKE Autopilot                              each session
 └── analytics/default.tfstate   ◄── analytics/    Pub/Sub schema, topics, subscriptions,     months, ~$0
                                                   BigQuery dataset/table/view, IAM          (added in 6B)

 Outputs flow one way, read with terraform_remote_state (read-only):
   foundation ──► data      network_id, psa_range_name, api_sa_email
   foundation ──► cluster   network_id, subnet_id, pods/services range names, nodes_sa_email
   data       ──► cluster   connection_name, private_ip (passed through for the manifests)
   foundation ──► analytics api_sa_email
```

| Layer | Owns | Protected by | Not in Terraform, on purpose |
|---|---|---|---|
| [`bootstrap`](../infra/terraform/bootstrap/) | State bucket (versioning, lifecycle keeps 10 old versions) | `force_destroy = false`, `deletion_policy = "PREVENT"`, `prevent_destroy` | — |
| [`foundation`](../infra/terraform/foundation/) | 17 APIs (`bigquerystorage` added in 6B; `disable_on_destroy = false`); `exit-ticket-api`, `exit-ticket-nodes`; 8 additive IAM grants; 3 secret **containers**; the `exit-ticket` repository + cleanup policy; `exit-ticket-vpc`, subnet with `pods`/`services`, PSA range + peering | `PREVENT` on SAs, repository, network, peering; `deletion_protection` on secrets | **Secret values** (they'd be in state in plain text): `scripts/create-app-secrets.sh` and pipes |
| [`data`](../infra/terraform/data/) | `exit-ticket-db` (public IP proxy-only + private IP `10.40.0.3`), database `exitticket`, IAM user `exit-ticket-api@PROJECT_ID.iam`; `activation_policy` variable (default `NEVER`) | `deletion_protection`, `deletion_policy = "PREVENT"` (all three), `settings.deletion_protection_enabled` | The `postgres` user and password; the in-database `GRANT`s |
| [`cluster`](../infra/terraform/cluster/) | `exit-ticket` Autopilot: private nodes, external IP endpoint off, DNS endpoint, node SA, Workload Identity, Secret Manager add-on | Nothing, on purpose (`deletion_protection = false`): it holds no data | The Kubernetes manifests (`kubectl`, Cloud Build in Phase 7) |
| [`analytics`](../infra/terraform/analytics/) ([6B](phase-6b-analytics.md)) | Avro schema `answer-event`; topics `answer-events` (schema, JSON) and `answer-events-dlq`; subscriptions `answer-events-to-bq` (BigQuery, DLQ after 5) and `answer-events-dlq-inspect`; dataset `exit_ticket_analytics` (us-east4), table `answer_events`, view `answer_events_clean`; 4 resource-level IAM grants | Dataset: `delete_contents_on_destroy = false`, `deletion_policy = "PREVENT"`, `prevent_destroy`. Table: `deletion_protection`, `prevent_destroy` | The data itself (rows written by Pub/Sub) |

Every layer: `versions.tf` identical (Terraform `~> 1.17.0`, `google ~> 8.11`, locked at 8.11.0 for `darwin_arm64`
and `linux_amd64`), a `backend.tf` with only its `prefix` (the bucket is passed at `init`), `terraform.tfvars.example`
committed and `terraform.tfvars` gitignored.

**Why layers:** blast radius and lifetime. `terraform destroy` in `cluster/` can only touch the one resource in its
state, which is what the per-session teardown needs.

---

## 2. What exists, and what it costs

| Layer | While a session runs | Between sessions |
|---|---|---|
| bootstrap | A few KB: ~$0 | ~$0 |
| foundation | ~$0: SAs, IAM, VPC, subnet, PSA peering are free; 3 secret versions are within the free tier; images ~100 MB, cents | Same |
| data | ~$0.011/hr compute + 10 GB SSD + public IPv4 | **Stopped**: disk + IP + one on-demand backup, ~$2–4/month |
| cluster | $0.10/hr fee (covered by GKE's free-tier credit for one Autopilot cluster) + Pod requests ~$0.015/hr | **Destroyed**: $0 |
| analytics | Usage only (KB of messages, rows, queries): ~$0 | Same. Nothing bills by the hour, so it **stays up** |
| Ingress (optional, kubectl) | ~$0.025/hr | Deleted |

A 3-hour session is about $0.40 gross, cents net of credits. The private IP and the peering add nothing.

---

## 3. Build from scratch

The order a new project would need. **Only the bootstrap step has been run from zero**; the other layers were
imported or created once, so treat this as the recipe, with the caveats in each step. 💰 marks where billing starts.

### 3.1 Prerequisites

Terraform 1.17.x, gcloud with the `exit-ticket` configuration, Application Default Credentials with a quota project
([pre-check §3](phase-5-precheck.md#1-results)), `cloud-sql-proxy` and `libpq` from Homebrew, the `storage` API on.
In each layer:
```bash
cp terraform.tfvars.example terraform.tfvars
```
and fill in `project_id` (foundation also `admin_member = "user:YOUR_EMAIL"`).

### 3.2 bootstrap (~$0) — [5A §4](phase-5a-bootstrap.md#4-build-from-scratch)

```bash
cd ~/projects/exit-ticket-5th-grade-math-science/infra/terraform/bootstrap
mv backend.tf backend.tf.off
terraform init
terraform plan -out=tfplan
terraform apply tfplan
rm tfplan
mv backend.tf.off backend.tf
terraform init -migrate-state -backend-config="bucket=$(gcloud config get project)-tfstate"
rm terraform.tfstate terraform.tfstate.backup
```
The bucket must exist before its own state can live in it, hence local state first, then the migration (answer
`yes`).

### 3.3 foundation (~$0) — [5B](phase-5b-foundation.md), [5C](phase-5c-network.md)

```bash
cd ~/projects/exit-ticket-5th-grade-math-science/infra/terraform/foundation
terraform init -backend-config="bucket=$(gcloud config get project)-tfstate"
terraform plan -out=tfplan
terraform apply tfplan
rm tfplan
```
Caveat (untested from zero): the `workloadIdentityUser` grant names the project's workload identity pool
(`PROJECT_ID.svc.id.goog`), which only exists once a GKE cluster has been created. On a brand-new project this one
grant may fail: apply the rest, create the cluster (§3.6), then apply foundation again.

### 3.4 Secret values (free) — [5B §6](phase-5b-foundation.md#6-secret-values-stay-out-of-terraform)

```bash
cd ~/projects/exit-ticket-5th-grade-math-science
scripts/create-app-secrets.sh
openssl rand -base64 32 | tr -d '\n' | gcloud secrets versions add postgres-admin-password --data-file=-
```
The script refuses a secret that already has a version. Values travel through pipes only.

### 3.5 data (💰) — [5D](phase-5d-cloud-sql.md)

```bash
cd ~/projects/exit-ticket-5th-grade-math-science/infra/terraform/data
terraform init -backend-config="bucket=$(gcloud config get project)-tfstate"
terraform plan -var="activation_policy=ALWAYS" -out=tfplan
terraform apply tfplan
rm tfplan
```
On a fresh project this **creates** the instance (with both IPs from the start), the database and the IAM user;
expect 10–15 minutes. Then set the `postgres` password from Secret Manager (it never goes through Terraform), via
the clipboard ([4A §4](phase-4a-cloud-sql.md#4-the-postgres-password)):
```bash
gcloud secrets versions access 1 --secret=postgres-admin-password | pbcopy
gcloud sql users set-password postgres --instance=exit-ticket-db --prompt-for-password
pbcopy < /dev/null
```

### 3.6 In-database GRANTs (one time) — [4A §7](phase-4a-cloud-sql.md#7-grants-as-postgres)

Tab 1, the proxy as **you** (public IP path):
```bash
cloud-sql-proxy --port=15432 $(gcloud config get project):us-east4:exit-ticket-db
```
Tab 2 (a new tab):
```bash
APP_USER="exit-ticket-api@$(gcloud config get project).iam"
PGPASSWORD="$(gcloud secrets versions access 1 --secret=postgres-admin-password)" /opt/homebrew/opt/libpq/bin/psql "host=127.0.0.1 port=15432 dbname=exitticket user=postgres" -v ON_ERROR_STOP=1 -c "GRANT CONNECT ON DATABASE exitticket TO \"$APP_USER\";" -c "GRANT USAGE, CREATE ON SCHEMA public TO \"$APP_USER\";"
```
Ctrl+C the proxy in Tab 1.

### 3.7 cluster (💰) — [5E](phase-5e-cluster.md)

```bash
cd ~/projects/exit-ticket-5th-grade-math-science/infra/terraform/cluster
terraform init -backend-config="bucket=$(gcloud config get project)-tfstate"
terraform plan -out=tfplan
terraform apply tfplan
rm tfplan
gcloud container clusters get-credentials exit-ticket --region=us-east4 --dns-endpoint
```

### 3.8 Images and manifests (💰 pods) — [4C §2](phase-4c-gke-cloud-sql.md#2-images), [5E §6](phase-5e-cluster.md#6-deploying-with---private-ip)

A fresh repository is empty: push images first (clean tree required) and put the printed digests into
`k8s/api.yaml`, `k8s/migrate-job.yaml` and `k8s/web.yaml`:
```bash
scripts/push-images.sh
```
Then deploy as in [§4](#4-starting-a-session), and seed once as the app ([4A §8](phase-4a-cloud-sql.md#8-migrations-and-seed-as-the-app)).

### 3.9 analytics (~$0) — [6B](phase-6b-analytics.md)

Needs only foundation, so it can be built any time after §3.3:
```bash
cd ~/projects/exit-ticket-5th-grade-math-science/infra/terraform/analytics
terraform init -backend-config="bucket=$(gcloud config get project)-tfstate"
terraform plan -out=tfplan
terraform apply tfplan
rm tfplan
```
The Pub/Sub service agent must exist first (it appears when the Pub/Sub API is enabled; 6B §4 shows the check).

---

## 4. Starting a session

**Tab 1.** Start Cloud SQL first: it's the long pole (11–13 minutes).
```bash
cd ~/projects/exit-ticket-5th-grade-math-science/infra/terraform/data
terraform apply -var="activation_policy=ALWAYS"
```
The plan must be only `~ activation_policy = "NEVER" -> "ALWAYS"`.

**Tab 2 (a new tab), at the same time.** The cluster (~4.5 minutes). The two layers have separate states and locks,
so they can run in parallel:
```bash
cd ~/projects/exit-ticket-5th-grade-math-science/infra/terraform/cluster
terraform apply
gcloud container clusters get-credentials exit-ticket --region=us-east4 --dns-endpoint
```
The plan must be `1 to add`.

**Tab 2, once Tab 1 says `Apply complete`** (the migrate Job needs the database):
```bash
cd ~/projects/exit-ticket-5th-grade-math-science
kubectl apply -f k8s/namespace.yaml
sed "s/PROJECT_ID/$(gcloud config get project)/g" k8s/configmap.yaml | kubectl apply -f -
sed "s/PROJECT_ID/$(gcloud config get project)/g" k8s/serviceaccount.yaml | kubectl apply -f -
sed "s/PROJECT_ID/$(gcloud config get project)/g" k8s/secretproviderclass.yaml | kubectl apply -f -
sed "s/PROJECT_ID/$(gcloud config get project)/g" k8s/migrate-job.yaml | kubectl apply -f -
kubectl -n exit-ticket wait --for=condition=complete job/migrate --timeout=300s
sed "s/PROJECT_ID/$(gcloud config get project)/g" k8s/api.yaml | kubectl apply -f -
sed "s/PROJECT_ID/$(gcloud config get project)/g" k8s/web.yaml | kubectl apply -f -
kubectl -n exit-ticket rollout status deployment/api --timeout=300s
kubectl -n exit-ticket rollout status deployment/web --timeout=300s
```
Autopilot starts with zero nodes: the Job's first node takes 1–3 minutes.

**Tab 1, to use the app** (left running):
```bash
kubectl -n exit-ticket port-forward svc/web 8080:80
```
→ http://localhost:8080/teacher.html (passcode: `gcloud secrets versions access 1 --secret=teacher-passcode | pbcopy`,
then `pbcopy < /dev/null`).

While Cloud SQL runs, **every plan in `data/` needs `-var="activation_policy=ALWAYS"`**; without it the plan shows
`ALWAYS -> NEVER` (= it would stop the instance).

---

## 5. Ending a session

Ctrl+C every port-forward, watch and proxy, then in one tab:
```bash
cd ~/projects/exit-ticket-5th-grade-math-science
kubectl delete -f k8s/ingress.yaml --wait=true
kubectl delete namespace exit-ticket
cd infra/terraform/cluster
terraform destroy
cd ../data
terraform apply
```
- Skip the Ingress line if none was applied; if it was, wait 2–3 minutes after the namespace for the NEG and the
  `k8s-fw-l7-…` rule to go before destroying the cluster ([3C](phase-3c-gke.md)).
- `destroy` must say `1 to destroy` (`google_container_cluster.main` only), ~5 minutes.
- The plain `apply` in `data/` must be only `~ activation_policy = "ALWAYS" -> "NEVER"`, ~1 minute.

Then the end-of-session block of [§6](#6-verification-checklist). Cloud SQL stays **stopped**; under the
[teardown policy](phase-4-cloud-sql.md#7-teardown) it's deleted if no session is planned within 7 days.

---

## 6. Verification checklist

### End of every session

```bash
cd ~/projects/exit-ticket-5th-grade-math-science
echo "--- clusters"; gcloud container clusters list
echo "--- forwarding rules"; gcloud compute forwarding-rules list
echo "--- backend services"; gcloud compute backend-services list
echo "--- NEGs"; gcloud compute network-endpoint-groups list
echo "--- disks"; gcloud compute disks list
echo "--- addresses"; gcloud compute addresses list
echo "--- sql"; gcloud sql instances describe exit-ticket-db --format="value(state,settings.activationPolicy,settings.deletionProtectionEnabled)"
echo "--- firewall rules"; gcloud compute firewall-rules list --format="table(name,network.basename(),sourceRanges.list())"
echo "--- bq subscription"; gcloud pubsub subscriptions describe answer-events-to-bq --format="value(state)"
echo "--- cluster state"; terraform -chdir=infra/terraform/cluster state list | wc -l
echo "--- processes"; pgrep -fl "cloud-sql-proxy|kubectl" || echo "none"
```
Healthy (2026-11-18): no clusters, forwarding rules, backend services, NEGs or disks; only `exit-ticket-psa` in
addresses; `STOPPED NEVER True`; `ACTIVE` for the BigQuery subscription (since 6B: anything else means messages are
piling up in its backlog and expire after 7 days); only the four `default-allow-*` rules (none on `exit-ticket-vpc`);
cluster state `0`; no processes.

### Any time

| Check | Command | Healthy |
|---|---|---|
| No drift | `terraform plan` in each layer ([§7](#7-drift)) | `No changes.` (cluster: `1 to add` when destroyed) |
| No secret values in state | `terraform -chdir=infra/terraform/foundation state pull \| grep -c secret_data` | `0` |
| Default SA has no roles | `gcloud projects get-iam-policy $(gcloud config get project) --flatten="bindings[].members" --filter="bindings.members:compute@developer.gserviceaccount.com" --format="table(bindings.role)"` | No rows |
| No keys | `gcloud iam service-accounts keys list --iam-account=exit-ticket-api@$(gcloud config get project).iam.gserviceaccount.com --managed-by=user` (and nodes) | `Listed 0 items.` |
| Secret readers | `gcloud secrets get-iam-policy session-secret --format="value(bindings.members)"` (and `teacher-passcode`) | Only `exit-ticket-api@…` |
| Cloud SQL network | `gcloud sql instances describe exit-ticket-db --flatten="ipAddresses" --format="table(ipAddresses.type,ipAddresses.ipAddress)"` | `PRIMARY`, `OUTGOING`, `PRIVATE 10.40.0.3` |
| Backups | `gcloud sql backups list --instance=exit-ticket-db --format="table(id,windowStartTime,type,status)"` | The 5D `ON_DEMAND`, `SUCCESSFUL` |
| Repo hygiene | `git ls-files \| grep -E 'tfstate\|tfplan\|\.tfvars$'` | Nothing |

### During a session

| Check | How | Healthy |
|---|---|---|
| Control plane | `curl` the external IP and the DNS endpoint ([5E §5](phase-5e-cluster.md#5-verifying-the-cluster)) | `000` and `403` |
| Nodes | `kubectl get nodes -o wide` | `EXTERNAL-IP <none>` |
| Pods | `kubectl -n exit-ticket get pods` | `api 2/2`, `web 1/1`, `migrate 0/2 Completed`, `RESTARTS 0` |
| Pod identity, secrets | [4C §8](phase-4c-gke-cloud-sql.md#8-workload-identity-checked-from-inside-the-pod) | `exit-ticket-api@…`; `secret env vars: 0` |
| Private database path | The `/proc/net/tcp` check ([5E §7](phase-5e-cluster.md#proof-the-proxy-uses-the-private-ip)) | `-> remote 10.40.0.3:3307 state 01` |

---

## 7. Drift

**Drift** is a difference between the real infrastructure and what Terraform's code and state say it should be,
caused by something other than Terraform: a Console click, a `gcloud` command, Google changing a default or
upgrading a cluster, another tool. Every `plan` starts with a **refresh** (reading each resource from the API), so
drift shows up as changes nobody wrote: an `~` that would put the code's value back.

**Check, 2026-11-18** (everything torn down, Cloud SQL stopped):
```bash
cd ~/projects/exit-ticket-5th-grade-math-science
terraform -chdir=infra/terraform/bootstrap plan -detailed-exitcode
terraform -chdir=infra/terraform/foundation plan -detailed-exitcode
terraform -chdir=infra/terraform/data plan -detailed-exitcode
terraform -chdir=infra/terraform/cluster plan -detailed-exitcode
```
**No drift anywhere.** For the cluster, the plan was written to `$TMPDIR` and only its summary printed (a full create is
~150 lines). `-detailed-exitcode` makes the result machine-readable: **0** = no changes, **1** = error, **2** = changes. That's
what a scheduled drift check in CI (Phase 7) would test.

| Layer | Expected | Result |
|---|---|---|
| bootstrap | `No changes`, exit 0 | ✅ `No changes`, exit 0 |
| foundation | `No changes`, exit 0 | ✅ `No changes`, exit 0 (37 resources and data sources refreshed) |
| data | `No changes`, exit 0 (stopped = the `NEVER` default) | ✅ `No changes`, exit 0 |
| cluster | `1 to add`, exit 2: **not drift**, the cluster is destroyed between sessions by design | ✅ `google_container_cluster.main will be created`, `Plan: 1 to add`, exit 2 |
| analytics (since 6B) | `No changes`, exit 0: add `terraform -chdir=infra/terraform/analytics plan -detailed-exitcode` to the block | ✅ exit 0 after the 6B apply and smoke test |

### How drift would be handled

| Situation | Response | Example from this project |
|---|---|---|
| Someone changed a setting by hand, and the code is right | **Revert**: `terraform apply` puts it back | Stopping Cloud SQL with `gcloud … patch` instead of Terraform: the next plan in `data/` would show it |
| The change is right and the code is out of date | **Adopt**: change the code to match, plan until `No changes` | `enable_dataplex_integration = true`, switched on by Google, written into the code in 5D |
| Another system legitimately owns the field | **Ignore it**: `lifecycle { ignore_changes = [...] }` | Considered for `activation_policy` (5D, rejected); typical for GKE node versions auto-upgraded by the release channel |
| Only read-only/computed values moved (new IP, version) | **Accept into state**: `terraform apply -refresh-only` | None so far |
| A resource was deleted by hand | Plan shows `+ create`: recreate it, or remove it from the code (and state with `terraform state rm`) if it should be gone | None so far |

Rules: never "fix" drift by applying a plan you haven't read; a drift plan that shows `-/+` (replacement) on
anything in `data/` means stop, as in 5D.

---

## 8. Troubleshooting

Only problems actually hit in Phase 5. Full tables in each part doc.

| Symptom | Cause | Fix | Where |
|---|---|---|---|
| `gcloud storage …` crashes: `module 'ssl' has no attribute 'PROTOCOL_TLSv1'` | gcloud on Homebrew's Python 3.14 / OpenSSL 4 | `export CLOUDSDK_PYTHON=/opt/homebrew/bin/python3.13` | [5A §8](phase-5a-bootstrap.md#8-troubleshooting) |
| Migrated local state looked "emptied" with a `.backup` left | That's how `-migrate-state` leaves the old file | Compare lineage and contents in the bucket; delete the local files | [5A §11](phase-5a-bootstrap.md#11-lessons-learned) |
| Lock file only had `darwin_arm64` | `init` locks only the current platform | `terraform providers lock -platform=darwin_arm64 -platform=linux_amd64` | [5A §11](phase-5a-bootstrap.md#11-lessons-learned) |
| Import plan said `7 to add` for IAM that already existed | One `for_each` import aimed at a non-`for_each` resource | One `import` block per grant; in an import plan, `add` means "not matched" | [5B §4](phase-5b-foundation.md#4-every-diff-and-what-was-done-about-it) |
| `~ labels { + … }` right after an import | Imports start with `labels = {}` | Expected; compare `effective_labels` | [5B §4](phase-5b-foundation.md#4-every-diff-and-what-was-done-about-it) |
| gcloud offers to enable an API during a read-only check | The command's service is off | Answer `N` | [5B §9](phase-5b-foundation.md#9-troubleshooting) |
| `HTTPError 409: … another operation was already in progress` | A Cloud SQL start still running | `gcloud sql operations wait OPERATION_ID --timeout=unlimited`, retry | [5D §3](phase-5d-cloud-sql.md#3-safety-first-the-backup) |
| Cloud SQL state `MAINTENANCE` right after a start | A starting instance is reported that way | Wait for the `UPDATE` operation | [5D §3](phase-5d-cloud-sql.md#3-safety-first-the-backup) |
| A plain plan in `data/` wants `ALWAYS -> NEVER` | The variable defaults to stopped | `-var="activation_policy=ALWAYS"` while running | [5D §9](phase-5d-cloud-sql.md#9-starting-and-stopping-through-terraform) |
| PSA range stays `RESERVED` with Cloud SQL on it | Compute's `status` doesn't track Service Networking use | Check the instance's `10.40.x` IP and the imported `/24` route | [5D §8](phase-5d-cloud-sql.md#8-verification-checklist) |
| gcloud table with `--flatten` and `[]` prints `None` | Flattened rows have no list | `--flatten="ipAddresses" --format="table(ipAddresses.type,…)"` | [5D §10](phase-5d-cloud-sql.md#10-troubleshooting) |
| Cluster create: `Error 400 … enable_master_authorized_networks should be enabled if private endpoint is enabled` | External IP endpoint off requires authorized networks on | `master_authorized_networks_config` with no `cidr_blocks` | [5E §4](phase-5e-cluster.md#4-creating-the-cluster) |
| `privateClusterConfig.publicEndpoint` shows an IP although the endpoint is off | Allocated, not enabled | `curl` it: `000` | [5E §5](phase-5e-cluster.md#5-verifying-the-cluster) |
| `kubectl get nodes` → `No resources found` on a new cluster | Autopilot runs zero nodes without workloads | Apply a workload | [5E §5](phase-5e-cluster.md#5-verifying-the-cluster) |
| `gcloud compute instances list` empty / `describe` not found while nodes exist | Autopilot hides its VMs | Use kubectl, the cluster's `describe`, state | [5E §5](phase-5e-cluster.md#autopilot-hides-its-node-vms) |
| `502 Bad Gateway` from nginx on `/api/…` | The api Service has no ready endpoint (database down) | Check `/readyz` and Cloud SQL | [5E §7](phase-5e-cluster.md#drill-stop-cloud-sql-through-terraform) |
| A `-w` watch "never finishes" | Watches run until Ctrl+C | Run it in its own tab | [5E §7](phase-5e-cluster.md#drill-stop-cloud-sql-through-terraform) |

---

## 9. End-of-project teardown

Only when the project is finished, and only in this order ([5C §6](phase-5c-network.md#6-destroy-order-and-the-peering-problem),
[5A §9](phase-5a-bootstrap.md#9-teardown)). Every step is a code change, a reviewed plan and an apply; each lock is
lifted deliberately.

| # | Layer | Lift first | Then | Why this order |
|---|---|---|---|---|
| 1 | Kubernetes | — | `kubectl delete -f k8s/ingress.yaml`, `kubectl delete namespace exit-ticket` | The in-cluster controllers delete the load balancer and NEGs |
| 2 | `cluster` | — (`deletion_protection = false` already) | `terraform destroy` | Nothing depends on it |
| 3 | `data` | Optionally a final export to Cloud Storage. `deletion_policy = "DELETE"` on all three, `deletion_protection = false`, `settings.deletion_protection_enabled = false`; apply | `terraform destroy` (the instance's backups go with it unless `retain_backups_on_delete`) | Cloud SQL must be gone before the peering can be removed |
| 4 | `analytics` | Optionally a final `bq extract` of `answer_events` to Cloud Storage. Table: `deletion_protection = false`, remove `prevent_destroy`. Dataset: `deletion_policy = "DELETE"`, remove `prevent_destroy`; `delete_contents_on_destroy` may stay `false` because Terraform deletes the table and view first; apply | `terraform destroy` | Its IAM grants name foundation's `exit-ticket-api`, and it reads foundation's state; destroying foundation first would leave grants for a deleted account and a remote-state read that fails |
| 5 | `foundation` | `deletion_policy = "REMOVE_PEERING"` on the PSA connection, `"DELETE"` on the network, SAs and repository, `deletion_protection = false` on the secrets; apply | `terraform destroy` (APIs stay on: `disable_on_destroy = false`) | Google can take days to release the producer subnet; `REMOVE_PEERING` deletes the peering directly once Cloud SQL is gone |
| 6 | `bootstrap` | Migrate its state back to local (remove `backend.tf`, `init -migrate-state`); `deletion_policy = "DELETE"`, `force_destroy = true`, remove `prevent_destroy`; apply | `terraform destroy` | It holds every other layer's state, so it goes last |

Afterwards: the [Phase 4 §7](phase-4-cloud-sql.md#7-teardown) verify list should be empty apart from the `default`
network, then `gcloud projects delete` if the whole project goes.

---

## 10. Known gaps

### Phase 4 gaps, now

| Phase 4 gap | Status |
|---|---|
| Compute Engine default SA has Editor | ✅ **Closed** in [5B](phase-5b-foundation.md#7-removing-editor-from-the-compute-engine-default-sa) |
| Everything created by hand | ✅ **Closed**: all four layers in Terraform; only secret values, the `postgres` password and the GRANTs are by hand, on purpose |
| Cloud SQL public IP only | 🟡 **Partly**: private IP added and used by the pods ([5D](phase-5d-cloud-sql.md#7-private-ip), [5E](phase-5e-cluster.md)); the public IP stays for the Mac (no authorized networks, connector enforcement) |
| No backups ever taken | 🟡 **Partly**: one on-demand backup ([5D §3](phase-5d-cloud-sql.md#3-safety-first-the-backup)); automated ones still never run (stopped at 07:00 UTC); PITR off |
| Cluster control plane public, nodes on the default network | ✅ **Closed**: private nodes on `exit-ticket-vpc`, external IP endpoint off, IAM-authorized DNS endpoint ([5E](phase-5e-cluster.md)) |
| One identity for api and migrate (api has DDL) | Open |
| Shared-core tier, no SLA, slow starts | Open (dev) |
| Cloud SQL roles on the project | Open (no per-instance IAM; IAM Conditions later) |
| Secret rotation manual | Open |
| Data Access logs off | Open |
| Ingress plain HTTP, ephemeral IP | Open (not used in Phase 5) |

### New in Phase 5

| Gap | Why it's acceptable now | Later |
|---|---|---|
| Build from scratch untested beyond bootstrap; the `workloadIdentityUser` grant may need a second foundation apply on a new project | Everything else was imported or created once and verified | A throwaway project, or Phase 7's pipeline |
| Anyone with Owner can read state and secrets | One-person project; no secret values in state | Phase 7: Cloud Build SA with access to the bucket only |
| Terraform runs from the Mac only; no CI plan, no scheduled drift check | Phase 7 | Cloud Build: `plan` on PRs, `plan -detailed-exitcode` on a schedule |
| `retain_backups_on_delete = false`, `ssl_mode` allows unencrypted (proxy enforces TLS anyway) | Delete triple-locked; connector enforcement | In-place changes when convenient |
| `private_endpoint_enforcement_enabled = false` on the cluster | Only this cluster is in the VPC | Turn on, or disable IP endpoints (DNS only) |
| The default network and its open SSH/RDP rules still exist | Nothing uses them | Phase 8: delete |
| No NetworkPolicy | One app | Phase 8: default-deny + web → api |
| Manifests applied by hand with `sed` | Phase 7 | Cloud Build |

---

## 11. Lessons learned

The ones that mattered most across 5A–5E; each part doc has its full table.

| Lesson | From |
|---|---|
| **Read every line of a plan**, including attributes you never wrote; look them up in the pinned provider's schema (`terraform providers schema -json`), not in memory or old guides | 5A (`deletion_policy`), 5C (`REMOVE_PEERING`), 5D (`enable_dataplex_integration`, `retain_backups_on_delete`) |
| **In an import plan, `add` means "not matched"**; the proof of an import is `No changes` | 5B |
| **Write import code from `describe`, field by field**: the Cloud SQL import was clean on the first plan | 5D |
| **Back up before the first change to anything stateful**, and check the backup says `SUCCESSFUL` | 5D |
| **Some rules live only in the API**: `validate` can't catch them; a refused create creates nothing, so check state and re-plan | 5E |
| **Verify behaviour, not fields**: an allocated endpoint IP (`curl` → `000`), a `RESERVED` range that's in use, hidden Autopilot VMs | 5D, 5E |
| **Layers by lifetime**: the cluster can be destroyed every session because nothing else lives in its state | 5A, 5E |
| **Defaults that fail safe**: `activation_policy` defaults to stopped; forgetting the flag is the cheap outcome | 5D |
| **Lock files need every platform** that will run Terraform (Mac and CI) | 5A |
| **One step at a time, every block labelled RUN NOW or REFERENCE, and every tab named** | 5B, 5E |

---

## 12. Interview talking points

**1. "I brought hand-built infrastructure under Terraform with zero recreation."** 34 resources imported (service
accounts, IAM, secrets, Artifact Registry, a live Cloud SQL instance) with `import` blocks; every diff explained; each
import proved by a `No changes` plan. Backups first for the database, and the saved plan checked as JSON for
replacements.

**2. "Layered state for blast radius."** Four root modules in one versioned, locked GCS bucket, split by lifetime. The
cluster layer is created and destroyed every session; the data layer has three delete locks; values flow one way
through outputs.

**3. "Private by default, identity over IP."** Custom VPC with no allow rules of our own; private nodes reach Google
APIs through Private Google Access (no NAT); Cloud SQL on a private IP through private services access; the GKE
control plane's external endpoint is off and kubectl uses the IAM-authorized DNS endpoint. I proved the pod's
connection goes to `10.40.0.3` from its socket table.

**4. "Secrets never touch state."** Terraform owns secret containers and access; values go straight into Secret
Manager through pipes. No keys anywhere: ADC on the Mac, Workload Identity in the cluster, IAM database login.

**5. "Least privilege, done safely."** Removed Editor from the default service account after checking services, VMs,
builds, keys and 30 days of audit logs, through an import-then-delete so the plan showed exactly one removal.

**6. "Resilience and cost as code."** Stopping Cloud SQL with `terraform apply` took the api out of rotation with zero
restarts, and starting it brought the same pod back. A session costs cents; between sessions only a stopped database
bills.

---

## ACE exam notes

**Infrastructure as code on Google Cloud**
- Terraform is Google's recommended IaC tool; **Infrastructure Manager** runs Terraform as a managed service (state
  in Google's storage); **Deployment Manager** is deprecated. **Config Connector** manages Google Cloud resources as
  Kubernetes objects.
- State in a **GCS backend**: versioning on, uniform bucket-level access, public access prevention; native locking.
  State can hold secrets in plain text: restrict access and keep secret values out.
- `terraform init` (providers, backend), `fmt`, `validate`, `plan` (refresh + diff), `apply`, `destroy`; `plan -out` +
  `apply PLANFILE` applies exactly what was reviewed; `-detailed-exitcode` for automation; `import` blocks adopt
  existing resources; `-refresh-only` accepts drift into state.
- Symbols: `+` create, `~` update in place, `-` destroy, **`-/+` replace** (destroy then create).

**Projects, APIs, IAM** (domain: setting up a cloud solution environment)
- APIs are enabled per project; disabling one can break dependents (`disable_on_destroy = false`).
- IAM resources: `_iam_policy` (authoritative, whole policy: can lock you out), `_iam_binding` (authoritative per
  role), **`_iam_member`** (additive, one member). gcloud: `add-iam-policy-binding` (additive) vs `set-iam-policy`.
- Default service accounts historically get Editor; remove it, use dedicated SAs; disable before deleting.

**Networking** (domain: planning and configuring)
- Custom-mode VPC, regional subnets, secondary ranges for VPC-native GKE, Private Google Access, Cloud NAT for
  internet egress, implied firewall rules (deny ingress, allow egress).
- Cloud SQL private IP via **private services access** (reserved range + `servicenetworking` peering; non-transitive;
  can't be removed once set) or Private Service Connect.
- GKE control plane: external IP endpoint (+ authorized networks), internal endpoint, **DNS-based endpoint**
  (IAM-authorized). Private nodes have internal IPs only.

**Operations** (domain: ensuring successful operation)
- Cloud SQL stop/start with `activation_policy`; one operation at a time (`gcloud sql operations list|wait`);
  backups automated (only while running) and on-demand; deletion protection.
- GKE Autopilot: Google-managed nodes (VMs not visible in Compute Engine), billed per Pod request, scales to zero;
  release channels; `get-credentials` for kubectl.
- Probes: liveness → restart, readiness → out of the Service, startup → gates the others.
